"""Bedrock RAG evaluation of the HR Knowledge Base: `uv run rag-eval start|status|cleanup`.

Complements `agentcore run eval` (which judges the whole agent from its traces): this job
evaluates retrieval + generation of the Knowledge Base alone against reference answers in
data/evals/rag-eval.jsonl (uploaded by `uv run kb-data upload`).
"""

import json
import sys
import time

import boto3

from hr_demo import config

USAGE = "usage: uv run rag-eval start|status|cleanup"
JOB_PREFIX = "ic-agentcore-demo-rag"
DATASET_KEY = "evals/rag-eval.jsonl"
OUTPUT_KEY = "eval-results/"
METRICS = [
    "Builtin.Correctness",
    "Builtin.Completeness",
    "Builtin.Faithfulness",
    "Builtin.Helpfulness",
    "Builtin.LogicalCoherence",
]
POLICY_NAME = "rag-eval"


def _profile_arn(account: str, model: str) -> str:
    return f"arn:aws:bedrock:{config.REGION}:{account}:inference-profile/{model}"


def _ensure_role(iam, account: str) -> str:
    """Service role Bedrock assumes to read the dataset, query the KB, call models and write results."""
    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": account}},
        }],
    }
    bucket = f"arn:aws:s3:::{config.KB_BUCKET}"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"], "Resource": [bucket, f"{bucket}/evals/*"]},
            {"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": f"{bucket}/eval-results/*"},
            {"Effect": "Allow", "Action": ["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"],
             "Resource": f"arn:aws:bedrock:{config.REGION}:{account}:knowledge-base/*"},
            {"Effect": "Allow",
             "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:GetInferenceProfile"],
             "Resource": ["arn:aws:bedrock:*::foundation-model/*", f"arn:aws:bedrock:*:{account}:inference-profile/*"]},
        ],
    }
    try:
        arn = iam.get_role(RoleName=config.RAG_EVAL_ROLE_NAME)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(
            RoleName=config.RAG_EVAL_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Bedrock RAG evaluation jobs for aws_bedrock_agentcore_demo",
        )["Role"]["Arn"]
        print(f"Role {config.RAG_EVAL_ROLE_NAME} created")
        time.sleep(10)  # IAM propagation before Bedrock tries to assume the role
    iam.put_role_policy(RoleName=config.RAG_EVAL_ROLE_NAME, PolicyName=POLICY_NAME, PolicyDocument=json.dumps(policy))
    return arn


def _knowledge_base_id() -> str:
    client = boto3.client("bedrock-agent", region_name=config.REGION)
    for page in client.get_paginator("list_knowledge_bases").paginate():
        for kb in page["knowledgeBaseSummaries"]:
            if kb["name"] == config.KNOWLEDGE_BASE_NAME:
                return kb["knowledgeBaseId"]
    sys.exit(f"Knowledge base {config.KNOWLEDGE_BASE_NAME} not found: run `agentcore deploy` first")


def start(bedrock) -> None:
    account = config.account_id()
    role_arn = _ensure_role(boto3.client("iam"), account)
    job_name = f"{JOB_PREFIX}-{time.strftime('%Y%m%d-%H%M%S')}"
    job = bedrock.create_evaluation_job(
        jobName=job_name,
        roleArn=role_arn,
        applicationType="RagEvaluation",
        evaluationConfig={"automated": {
            "datasetMetricConfigs": [{
                "taskType": "QuestionAndAnswer",
                "dataset": {"name": "HrHandbookQA", "datasetLocation": {"s3Uri": f"s3://{config.KB_BUCKET}/{DATASET_KEY}"}},
                "metricNames": METRICS,
            }],
            "evaluatorModelConfig": {"bedrockEvaluatorModels": [{"modelIdentifier": config.RAG_EVAL_EVALUATOR_MODEL}]},
        }},
        inferenceConfig={"ragConfigs": [{"knowledgeBaseConfig": {"retrieveAndGenerateConfig": {
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": _knowledge_base_id(),
                "modelArn": _profile_arn(account, config.RAG_EVAL_GENERATOR_MODEL),
            },
        }}}]},
        outputDataConfig={"s3Uri": f"s3://{config.KB_BUCKET}/{OUTPUT_KEY}"},
    )
    print(f"Job {job_name} started: {job['jobArn']}\nCheck with `uv run rag-eval status` (usually 15-40 min).")


def status(bedrock) -> None:
    jobs = bedrock.list_evaluation_jobs(nameContains=JOB_PREFIX, sortBy="CreationTime", sortOrder="Descending")
    if not jobs["jobSummaries"]:
        sys.exit("No RAG evaluation jobs yet: run `uv run rag-eval start`")
    for summary in jobs["jobSummaries"][:5]:
        print(f"{summary['jobName']}  {summary['status']}  {summary['creationTime']:%Y-%m-%d %H:%M}")
    latest = bedrock.get_evaluation_job(jobIdentifier=jobs["jobSummaries"][0]["jobArn"])
    print(f"\nLatest results: {latest['outputDataConfig']['s3Uri']}")
    print(f"Console: https://{config.REGION}.console.aws.amazon.com/bedrock/home?region={config.REGION}#/eval/evaluation")
    if latest.get("failureMessages"):
        print("Failures:", *latest["failureMessages"], sep="\n  ")


def cleanup() -> None:
    iam = boto3.client("iam")
    try:
        iam.delete_role_policy(RoleName=config.RAG_EVAL_ROLE_NAME, PolicyName=POLICY_NAME)
        iam.delete_role(RoleName=config.RAG_EVAL_ROLE_NAME)
        print(f"Role {config.RAG_EVAL_ROLE_NAME} deleted (job results stay in s3://{config.KB_BUCKET}/{OUTPUT_KEY})")
    except iam.exceptions.NoSuchEntityException:
        print(f"Role {config.RAG_EVAL_ROLE_NAME} not found")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    bedrock = boto3.client("bedrock", region_name=config.REGION)
    match command:
        case "start":
            start(bedrock)
        case "status":
            status(bedrock)
        case "cleanup":
            cleanup()
        case _:
            sys.exit(USAGE)
