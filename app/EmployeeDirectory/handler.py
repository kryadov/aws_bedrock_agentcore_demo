"""EmployeeDirectory: AgentCore Gateway Lambda target, exposed to the agent as MCP tools.

Gateway calls the function with the tool arguments as the event and the tool name in
context.client_context.custom["bedrockAgentCoreToolName"] ("<target>___<tool>").

The database is reached through the RDS Data API (HTTPS + IAM) because the CLI-generated Lambda
has no VPC. The CLI also can't set Lambda env vars, so the names below must match .env.example
(DB_CLUSTER, DB_NAME, DB_READER_SECRET) — `uv run hr-db create` creates exactly these.

The tools return full rows, sensitive columns included, on purpose: this demo shows the
guardrail stopping salaries/SSNs on output. A real service would expose a restricted view.
"""

import json
import time
from functools import cache

import boto3
from botocore.exceptions import ClientError

CLUSTER_NAME = "ic-agentcore-demo-hr"
DATABASE = "hr"
READER_SECRET = "ic-agentcore-demo/hr-db/reader"  # read-only DB user hr_mcp
MAX_ROWS = 20

_SELECT = """
SELECT e.employee_id, e.full_name, e.title, e.team, m.full_name AS manager, e.location,
       e.hired_on::text AS hired_on, e.work_email, e.phone, e.home_address,
       e.base_salary_usd::float AS base_salary_usd, e.ssn, e.bank_account
FROM employees e LEFT JOIN employees m ON m.employee_id = e.manager_id
"""

LIMIT = f" LIMIT {MAX_ROWS}"

QUERIES = {
    # confidential teams (unannounced projects) are left out of the overview, but not out of search
    "list_employees": _SELECT
    + "JOIN teams t ON t.name = e.team WHERE NOT t.confidential AND e.team ILIKE :q ORDER BY e.team, e.full_name"
    + LIMIT,
    "search_employees": _SELECT
    + "WHERE e.full_name ILIKE :q OR e.team ILIKE :q OR e.title ILIKE :q ORDER BY e.full_name"
    + LIMIT,
    "get_direct_reports": _SELECT + "WHERE m.full_name ILIKE :q ORDER BY e.full_name" + LIMIT,
}
ARGUMENT = {"list_employees": "team", "search_employees": "query", "get_direct_reports": "manager_name"}
OPTIONAL = {"list_employees"}  # empty argument = all rows


@cache
def _target(region: str, account: str) -> tuple[str, str]:
    cluster_arn = f"arn:aws:rds:{region}:{account}:cluster:{CLUSTER_NAME}"
    secret_arn = boto3.client("secretsmanager", region_name=region).describe_secret(SecretId=READER_SECRET)["ARN"]
    return cluster_arn, secret_arn


def _query(region: str, account: str, sql: str, value: str) -> list[dict]:
    cluster_arn, secret_arn = _target(region, account)
    client = boto3.client("rds-data", region_name=region)
    for attempt in range(8):
        try:
            response = client.execute_statement(
                resourceArn=cluster_arn,
                secretArn=secret_arn,
                database=DATABASE,
                sql=sql,
                parameters=[{"name": "q", "value": {"stringValue": f"%{value}%"}}],
                formatRecordsAs="JSON",
            )
            return json.loads(response.get("formattedRecords") or "[]")
        except ClientError as e:
            # Serverless v2 with 0 ACU auto-pauses; the first call after a pause wakes it up (~15 s)
            if e.response["Error"]["Code"] != "DatabaseResumingException" or attempt == 7:
                raise
            time.sleep(5)
    return []


def lambda_handler(event, context):
    tool = context.client_context.custom["bedrockAgentCoreToolName"].split("___")[-1]
    if tool not in QUERIES:
        return {"error": f"unknown tool {tool}"}
    value = str(event.get(ARGUMENT[tool], "")).strip()
    if not value and tool not in OPTIONAL:
        return {"error": f"argument {ARGUMENT[tool]} is required"}
    _, _, _, region, account, *_ = context.invoked_function_arn.split(":")
    rows = _query(region, account, QUERIES[tool], value)
    return {"employees": rows, "count": len(rows)}
