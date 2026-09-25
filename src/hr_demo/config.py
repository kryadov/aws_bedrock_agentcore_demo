"""Settings loaded from .env, falling back to .env.example (the single source of defaults)."""

import os
from functools import cache
from pathlib import Path

import boto3
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENTCORE_DIR = PROJECT_ROOT / "agentcore"
DATA_DIR = PROJECT_ROOT / "data"

# load_dotenv never overrides already-set variables, so precedence is:
# real environment > .env > .env.example
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / ".env.example")

# An empty AWS_PROFILE would make boto3 look for a profile named "" — drop it instead.
if not os.environ.get("AWS_PROFILE"):
    os.environ.pop("AWS_PROFILE", None)


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set; check .env / .env.example")
    return value


REGION = _require("AWS_REGION")
PROJECT_NAME = _require("PROJECT_NAME")
RUNTIME_NAME = _require("RUNTIME_NAME")
# Physical KB name created by the CLI: <project>_<kb>
KNOWLEDGE_BASE_NAME = f"{PROJECT_NAME}_{_require('KB_NAME')}"
RUNTIME_ARN = os.environ.get("RUNTIME_ARN", "")

# The repo holds no account id: {account} in KB_BUCKET is the account of the current credentials
# (bucket names are global), and CLI files carry PLACEHOLDER_ACCOUNT until `uv run stack up`.
PLACEHOLDER_ACCOUNT = "123456789012"
KB_BUCKET_TEMPLATE = _require("KB_BUCKET")
KB_PREFIX = _require("KB_PREFIX")

DB_CLUSTER = _require("DB_CLUSTER")
DB_NAME = _require("DB_NAME")
DB_READER_SECRET = _require("DB_READER_SECRET")
DB_VPC_ID = os.environ.get("DB_VPC_ID", "")

GUARDRAIL_NAME = _require("GUARDRAIL_NAME")
GUARDRAIL_BLOCKED_INPUT = _require("GUARDRAIL_BLOCKED_INPUT")
GUARDRAIL_BLOCKED_OUTPUT = _require("GUARDRAIL_BLOCKED_OUTPUT")

RAG_EVAL_GENERATOR_MODEL = _require("RAG_EVAL_GENERATOR_MODEL")
RAG_EVAL_EVALUATOR_MODEL = _require("RAG_EVAL_EVALUATOR_MODEL")
RAG_EVAL_ROLE_NAME = _require("RAG_EVAL_ROLE_NAME")


@cache
def account_id() -> str:
    return boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]


def kb_bucket(account: str | None = None) -> str:
    return KB_BUCKET_TEMPLATE.format(account=account or account_id())


def __getattr__(name: str) -> str:
    # config.KB_BUCKET calls STS on first use only, not on import
    if name == "KB_BUCKET":
        return kb_bucket()
    raise AttributeError(name)
