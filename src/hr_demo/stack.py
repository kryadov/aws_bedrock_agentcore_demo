"""Create or delete the whole demo environment in AWS: `uv run stack up|down|status [-y]`.

up    (idempotent, also used to apply changes; first writes the account into agentcore/*.json)
  1. S3 bucket + HR documents            kb_data.upload
  2. HR database + schema/data            hr_db.create       (Aurora Serverless v2, Data API; ~10 min first time)
  3. Bedrock guardrail + version          guardrail.create   (writes envVars into agentcore.json)
  4. CloudFormation stack                 agentcore deploy -y (Runtime, Memory, KB, Gateway + Lambda, Evaluator, IAM)
  5. KB indexing                          agentcore run ingest

down  (reverse order)
  1. CloudFormation stack                 delete_stack + wait (the CLI's `remove all` would also wipe agentcore.json)
  2. Log groups not owned by the stack    /aws/bedrock-agentcore/runtimes/<runtimeId>-*, /aws/lambda/<project>-*
  3. HR database                         hr_db.delete
  4. Guardrail, RAG eval role, S3 bucket  guardrail.delete, rag_eval.cleanup, kb_data.delete
  5. agentcore/.cli/deployed-state.json   reset to {"targets": {}}, account in agentcore/*.json -> placeholder

The account-wide CDK bootstrap stack (CDKToolkit) is shared with other projects and is left alone.
"""

import json
import os
import shutil
import subprocess
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError, WaiterError

from hr_demo import config, guardrail, hr_db, kb_data, project, rag_eval

USAGE = "usage: uv run stack up|down|status [-y]"
TARGET = "default"
STACK_NAME = f"AgentCore-{config.PROJECT_NAME}-{TARGET}"
RUNTIME_LOG_PREFIX = "/aws/bedrock-agentcore/runtimes/"
LAMBDA_LOG_PREFIX = f"/aws/lambda/{config.PROJECT_NAME}-"  # gateway tool Lambdas


def _step(title: str) -> None:
    print(f"\n=== {title}")


def _identity() -> dict:
    try:
        return boto3.client("sts", region_name=config.REGION).get_caller_identity()
    except (BotoCoreError, ClientError) as e:  # no credentials, expired SSO token, …
        sys.exit(f"No valid AWS credentials ({e}). Run `aws sso login` or set AWS_PROFILE in .env")


def _confirm(action: str, assume_yes: bool) -> None:
    identity = _identity()
    print(f"{action}\n  account {identity['Account']}, region {config.REGION}\n  as {identity['Arn']}")
    if not assume_yes and input("Continue? [y/N] ").strip().lower() != "y":
        sys.exit("Aborted")


def _agentcore(*args: str) -> None:
    exe = shutil.which("agentcore")  # resolves agentcore.cmd on Windows
    if not exe:
        sys.exit("agentcore CLI not found: npm install -g @aws/agentcore")
    env = dict(os.environ)
    # boto3 honors AWS_DEFAULT_PROFILE, the CLI's AWS SDK for JavaScript only AWS_PROFILE:
    # hand the CLI the same profile our boto3 steps used
    profile = boto3.Session().profile_name
    if profile and profile != "default":
        env.setdefault("AWS_PROFILE", profile)
    subprocess.run([exe, *args], cwd=config.PROJECT_ROOT, env=env, check=True)


def _stack(cfn) -> dict | None:
    try:
        return cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0]
    except ClientError as e:
        if "does not exist" in str(e):
            return None
        raise


def _runtime_ids(cfn) -> list[str]:
    """Physical ids of the stack's AgentCore runtimes (the ARN's last segment)."""
    ids = []
    for page in cfn.get_paginator("list_stack_resources").paginate(StackName=STACK_NAME):
        for r in page["StackResourceSummaries"]:
            if r["ResourceType"] == "AWS::BedrockAgentCore::Runtime" and r.get("PhysicalResourceId"):
                ids.append(r["PhysicalResourceId"].rsplit("/", 1)[-1])
    return ids


def _delete_log_groups(prefixes: list[str]) -> None:
    logs = boto3.client("logs", region_name=config.REGION)
    for prefix in prefixes:
        for page in logs.get_paginator("describe_log_groups").paginate(logGroupNamePrefix=prefix):
            for group in page["logGroups"]:
                logs.delete_log_group(logGroupName=group["logGroupName"])
                print(f"Log group {group['logGroupName']} deleted")


def up(assume_yes: bool) -> None:
    _confirm(f"Create/update {STACK_NAME}, bucket {config.KB_BUCKET}, database {config.DB_CLUSTER}, "
             f"guardrail {config.GUARDRAIL_NAME}", assume_yes)
    project.set_account(config.account_id())
    _step("1/5 S3 bucket and HR documents")
    kb_data.upload(boto3.client("s3", region_name=config.REGION))
    _step("2/5 HR database")
    hr_db.create()
    _step("3/5 Guardrail")
    guardrail.create(boto3.client("bedrock", region_name=config.REGION))
    _step("4/5 agentcore deploy")
    cfn = boto3.client("cloudformation", region_name=config.REGION)
    stack = _stack(cfn)
    if stack and stack["StackStatus"] == "ROLLBACK_COMPLETE":
        # a failed first create leaves an empty stack that can't be updated, only deleted
        print(f"{STACK_NAME} is ROLLBACK_COMPLETE after a failed create: deleting it first")
        cfn.delete_stack(StackName=STACK_NAME)
        cfn.get_waiter("stack_delete_complete").wait(StackName=STACK_NAME)
    _agentcore("deploy", "-y")
    _step("5/5 Knowledge base ingestion")
    _agentcore("run", "ingest", "--name", "HrDocs")
    _step("Done")
    status()


def down(assume_yes: bool) -> None:
    _confirm(f"DELETE {STACK_NAME} (runtime, memory with all sessions, KB, gateway), bucket {config.KB_BUCKET}, "
             f"database {config.DB_CLUSTER}, guardrail {config.GUARDRAIL_NAME}, role {config.RAG_EVAL_ROLE_NAME}",
             assume_yes)
    cfn = boto3.client("cloudformation", region_name=config.REGION)

    _step("1/5 CloudFormation stack")
    runtime_ids: list[str] = []
    if _stack(cfn):
        runtime_ids = _runtime_ids(cfn)  # read before the stack is gone
        cfn.delete_stack(StackName=STACK_NAME)
        print(f"Deleting {STACK_NAME} (several minutes)…")
        try:
            cfn.get_waiter("stack_delete_complete").wait(
                StackName=STACK_NAME, WaiterConfig={"Delay": 15, "MaxAttempts": 120})
        except WaiterError as e:
            sys.exit(f"Stack deletion failed: {e}. See the CloudFormation console, fix and rerun `down`.")
        print(f"Stack {STACK_NAME} deleted")
    else:
        print(f"Stack {STACK_NAME} not found, skipping")

    _step("2/5 Log groups")
    _delete_log_groups([f"{RUNTIME_LOG_PREFIX}{i}-" for i in runtime_ids] + [LAMBDA_LOG_PREFIX])

    _step("3/5 HR database")
    hr_db.delete()

    _step("4/5 Guardrail, RAG eval role, S3 bucket")
    guardrail.delete(boto3.client("bedrock", region_name=config.REGION))
    rag_eval.cleanup()
    kb_data.delete(boto3.client("s3", region_name=config.REGION))

    _step("5/5 Local deployed state")
    project.STATE_FILE.write_text(json.dumps({"targets": {}}, indent=2) + "\n", encoding="utf-8")
    project.set_account(config.PLACEHOLDER_ACCOUNT)
    print(f"{project.STATE_FILE.relative_to(config.PROJECT_ROOT)} reset, account placeholder restored")


def status() -> None:
    identity = _identity()
    cfn = boto3.client("cloudformation", region_name=config.REGION)
    stack = _stack(cfn)
    guardrail_id = guardrail.find_id(boto3.client("bedrock", region_name=config.REGION))
    bucket = kb_data.bucket_exists(boto3.client("s3", region_name=config.REGION))
    try:
        arn = project.runtime_arn()
    except RuntimeError:
        arn = "—"
    print(f"Account    {identity['Account']} / {config.REGION}")
    print(f"Stack      {STACK_NAME}: {stack['StackStatus'] if stack else 'not deployed'}")
    print(f"Runtime    {arn}")
    print(f"Guardrail  {config.GUARDRAIL_NAME}: {guardrail_id or 'not found'}")
    print(f"Bucket     {config.KB_BUCKET}: {'exists' if bucket else 'not found'}")
    hr_db.status()


def main() -> None:
    args = sys.argv[1:]
    assume_yes = "-y" in args or "--yes" in args
    command = next((a for a in args if not a.startswith("-")), "")
    match command:
        case "up":
            up(assume_yes)
        case "down":
            down(assume_yes)
        case "status":
            status()
        case _:
            sys.exit(USAGE)
