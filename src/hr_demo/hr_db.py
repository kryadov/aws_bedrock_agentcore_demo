"""HR database on Aurora PostgreSQL Serverless v2: `uv run hr-db create|seed|query|status|delete`.

The agentcore CLI has no RDS resource, so this script owns the database:

  create   own isolated VPC (or DB_VPC_ID) → subnet group → cluster with Data API,
           RDS-managed master secret, 0–1 ACU (auto-pause) → instance → reader secret → seed
  seed     data/db/schema.sql + seed.sql (master user), then the read-only role hr_mcp
  query    run one SQL statement as the master user, e.g. uv run hr-db query "SELECT * FROM teams"
  delete   instance → cluster (no final snapshot) → subnet group → own VPC → reader secret

Everything goes through the RDS Data API (HTTPS + IAM), which is also what the EmployeeDirectory
Lambda uses: no VPC networking, no security-group rules, no bastion.
"""

import json
import re
import secrets
import sys
import time

import boto3
from botocore.exceptions import ClientError

from hr_demo import config

USAGE = "usage: uv run hr-db create|seed|query \"<sql>\"|status|delete"
SQL_DIR = config.DATA_DIR / "db"
INSTANCE = f"{config.DB_CLUSTER}-1"
MASTER_USER = "hr_admin"
READER_USER = "hr_mcp"
TAGS = [{"Key": "Project", "Value": "ic-agentcore-demo"}]
VPC_CIDR = "10.250.0.0/24"
SUBNET_CIDRS = ["10.250.0.0/25", "10.250.0.128/25"]


def _rds():
    return boto3.client("rds", region_name=config.REGION)


def _cluster(rds) -> dict | None:
    try:
        return rds.describe_db_clusters(DBClusterIdentifier=config.DB_CLUSTER)["DBClusters"][0]
    except rds.exceptions.DBClusterNotFoundFault:
        return None


def _ec2():
    return boto3.client("ec2", region_name=config.REGION)


def _own_vpc(ec2) -> str | None:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [config.DB_CLUSTER]}])["Vpcs"]
    return vpcs[0]["VpcId"] if vpcs else None


def _subnets() -> list[str]:
    """One subnet per AZ (Aurora needs two AZs): DB_VPC_ID if set, else an own isolated VPC.

    The own VPC has no internet gateway and no routes out: nobody connects to the database over the
    network, everything goes through the Data API. Existing VPCs of the account are never used.
    """
    ec2 = _ec2()
    vpc_id = config.DB_VPC_ID or _own_vpc(ec2)
    if not vpc_id:
        tags = [{"ResourceType": "vpc", "Tags": [{"Key": "Name", "Value": config.DB_CLUSTER}, *TAGS]}]
        vpc_id = ec2.create_vpc(CidrBlock=VPC_CIDR, TagSpecifications=tags)["Vpc"]["VpcId"]
        ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id])
        zones = [z["ZoneName"] for z in ec2.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]}])["AvailabilityZones"]][:2]
        for zone, cidr in zip(zones, SUBNET_CIDRS):
            ec2.create_subnet(VpcId=vpc_id, CidrBlock=cidr, AvailabilityZone=zone, TagSpecifications=[
                {"ResourceType": "subnet", "Tags": [{"Key": "Name", "Value": f"{config.DB_CLUSTER}-{zone}"}, *TAGS]}])
        print(f"VPC {vpc_id} ({VPC_CIDR}, no internet access) created")
    by_az = {}
    for subnet in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]:
        by_az.setdefault(subnet["AvailabilityZone"], subnet["SubnetId"])
    if len(by_az) < 2:
        sys.exit(f"VPC {vpc_id} has subnets in fewer than 2 AZs")
    print(f"VPC {vpc_id}, subnets {', '.join(by_az.values())}")
    return list(by_az.values())


def _delete_own_vpc() -> None:
    ec2 = _ec2()
    vpc_id = _own_vpc(ec2)
    if not vpc_id:
        return
    for subnet in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]:
        ec2.delete_subnet(SubnetId=subnet["SubnetId"])
    ec2.delete_vpc(VpcId=vpc_id)
    print(f"VPC {vpc_id} deleted")


def _reader_password() -> str:
    """Password of hr_mcp from the reader secret; the secret is created on first use."""
    sm = boto3.client("secretsmanager", region_name=config.REGION)
    try:
        return json.loads(sm.get_secret_value(SecretId=config.DB_READER_SECRET)["SecretString"])["password"]
    except sm.exceptions.ResourceNotFoundException:
        password = secrets.token_urlsafe(24)
        sm.create_secret(
            Name=config.DB_READER_SECRET,
            Description="Read-only user of the HR demo database (EmployeeDirectory Lambda)",
            SecretString=json.dumps({"username": READER_USER, "password": password}),
            Tags=TAGS,
        )
        print(f"Secret {config.DB_READER_SECRET} created")
        return password


def _execute(cluster: dict, sql: str) -> dict:
    client = boto3.client("rds-data", region_name=config.REGION)
    for attempt in range(12):
        try:
            return client.execute_statement(
                resourceArn=cluster["DBClusterArn"],
                secretArn=cluster["MasterUserSecret"]["SecretArn"],
                database=config.DB_NAME,
                sql=sql,
                formatRecordsAs="JSON",
            )
        except ClientError as e:
            # 0 ACU clusters auto-pause; the first statement after a pause resumes them
            if e.response["Error"]["Code"] != "DatabaseResumingException" or attempt == 11:
                raise
            print("  database is resuming…")
            time.sleep(5)
    raise AssertionError("unreachable")


def _statements(path) -> list[str]:
    """Split a .sql file into statements: full-line comments dropped, ';' at line end separates."""
    text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*$", text, flags=re.MULTILINE) if s.strip()]


def _require_cluster(rds) -> dict:
    cluster = _cluster(rds)
    if not cluster or cluster["Status"] != "available":
        sys.exit(f"Cluster {config.DB_CLUSTER} is not available: run `uv run hr-db create`")
    return cluster


def seed() -> None:
    cluster = _require_cluster(_rds())
    for name in ("schema.sql", "seed.sql"):
        for statement in _statements(SQL_DIR / name):
            _execute(cluster, statement)
        print(f"Applied data/db/{name}")
    password = _reader_password()
    for statement in (
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{READER_USER}') "
        f"THEN CREATE ROLE {READER_USER} LOGIN; END IF; END $$",
        f"ALTER ROLE {READER_USER} WITH LOGIN PASSWORD '{password}'",
        f"GRANT CONNECT ON DATABASE {config.DB_NAME} TO {READER_USER}",
        f"GRANT USAGE ON SCHEMA public TO {READER_USER}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER_USER}",
    ):
        _execute(cluster, statement)
    print(f"Read-only role {READER_USER} granted SELECT")


def create() -> None:
    rds = _rds()
    if not _cluster(rds):
        try:
            rds.describe_db_subnet_groups(DBSubnetGroupName=config.DB_CLUSTER)
        except rds.exceptions.DBSubnetGroupNotFoundFault:
            rds.create_db_subnet_group(
                DBSubnetGroupName=config.DB_CLUSTER,
                DBSubnetGroupDescription="HR demo database (aws_bedrock_agentcore_demo)",
                SubnetIds=_subnets(),
                Tags=TAGS,
            )
        rds.create_db_cluster(
            DBClusterIdentifier=config.DB_CLUSTER,
            Engine="aurora-postgresql",
            DatabaseName=config.DB_NAME,
            MasterUsername=MASTER_USER,
            ManageMasterUserPassword=True,  # password lives in an RDS-managed Secrets Manager secret
            EnableHttpEndpoint=True,  # RDS Data API
            ServerlessV2ScalingConfiguration={"MinCapacity": 0, "MaxCapacity": 1, "SecondsUntilAutoPause": 600},
            DBSubnetGroupName=config.DB_CLUSTER,
            StorageEncrypted=True,
            BackupRetentionPeriod=1,
            DeletionProtection=False,
            Tags=TAGS,
        )
        print(f"Cluster {config.DB_CLUSTER} creating…")
    try:
        rds.describe_db_instances(DBInstanceIdentifier=INSTANCE)
    except rds.exceptions.DBInstanceNotFoundFault:
        rds.create_db_instance(
            DBInstanceIdentifier=INSTANCE,
            DBClusterIdentifier=config.DB_CLUSTER,
            DBInstanceClass="db.serverless",
            Engine="aurora-postgresql",
            Tags=TAGS,
        )
        print(f"Instance {INSTANCE} creating (~10 min)…")
    rds.get_waiter("db_instance_available").wait(DBInstanceIdentifier=INSTANCE, WaiterConfig={"Delay": 20, "MaxAttempts": 90})
    cluster = _cluster(rds)
    if not cluster.get("HttpEndpointEnabled"):
        rds.enable_http_endpoint(ResourceArn=cluster["DBClusterArn"])
    print(f"Cluster {config.DB_CLUSTER} available, Data API on")
    seed()


def query(sql: str) -> None:
    response = _execute(_require_cluster(_rds()), sql)
    print(json.dumps(json.loads(response.get("formattedRecords") or "[]"), indent=2, ensure_ascii=False)
          if "formattedRecords" in response else f"{response.get('numberOfRecordsUpdated', 0)} rows affected")


def status() -> None:
    cluster = _cluster(_rds())
    if not cluster:
        print(f"Cluster {config.DB_CLUSTER}: not found")
        return
    scaling = cluster.get("ServerlessV2ScalingConfiguration", {})
    print(f"Cluster {config.DB_CLUSTER}: {cluster['Status']}, {cluster['Engine']} {cluster['EngineVersion']}, "
          f"{scaling.get('MinCapacity')}–{scaling.get('MaxCapacity')} ACU, Data API {cluster.get('HttpEndpointEnabled')}")


def delete() -> None:
    rds = _rds()
    try:
        rds.delete_db_instance(DBInstanceIdentifier=INSTANCE)
        print(f"Instance {INSTANCE} deleting…")
        rds.get_waiter("db_instance_deleted").wait(DBInstanceIdentifier=INSTANCE, WaiterConfig={"Delay": 20, "MaxAttempts": 90})
    except rds.exceptions.DBInstanceNotFoundFault:
        print(f"Instance {INSTANCE} not found, skipping")
    if _cluster(rds):
        rds.delete_db_cluster(DBClusterIdentifier=config.DB_CLUSTER, SkipFinalSnapshot=True)
        print(f"Cluster {config.DB_CLUSTER} deleting (the RDS-managed master secret goes with it)…")
        while _cluster(rds):
            time.sleep(15)
        print(f"Cluster {config.DB_CLUSTER} deleted")
    else:
        print(f"Cluster {config.DB_CLUSTER} not found, skipping")
    try:
        rds.delete_db_subnet_group(DBSubnetGroupName=config.DB_CLUSTER)
        print(f"Subnet group {config.DB_CLUSTER} deleted")
    except rds.exceptions.DBSubnetGroupNotFoundFault:
        pass
    _delete_own_vpc()
    sm = boto3.client("secretsmanager", region_name=config.REGION)
    try:
        sm.delete_secret(SecretId=config.DB_READER_SECRET, ForceDeleteWithoutRecovery=True)
        print(f"Secret {config.DB_READER_SECRET} deleted")
    except sm.exceptions.ResourceNotFoundException:
        pass


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    match command:
        case "create":
            create()
        case "seed":
            seed()
        case "query" if len(sys.argv) > 2:
            query(sys.argv[2])
        case "status":
            status()
        case "delete":
            delete()
        case _:
            sys.exit(USAGE)
