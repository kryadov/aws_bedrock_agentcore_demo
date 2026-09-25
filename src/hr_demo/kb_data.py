"""Upload the HR handbook to S3 or delete the bucket: `uv run kb-data upload|delete`.

The CLI creates the Knowledge Base and its S3 data source but not the bucket itself.
After an upload, re-index with `agentcore run ingest`.
"""

import sys

import boto3
from botocore.exceptions import ClientError

from hr_demo import config

USAGE = "usage: uv run kb-data upload|delete"
KB_DIR = config.DATA_DIR / "kb"
EVALS_DIR = config.DATA_DIR / "evals"
EVALS_PREFIX = "evals/"


def bucket_exists(s3) -> bool:
    try:
        s3.head_bucket(Bucket=config.KB_BUCKET)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            return False
        raise


def _create_bucket(s3) -> None:
    s3.create_bucket(
        Bucket=config.KB_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": config.REGION},
    )
    s3.put_public_access_block(
        Bucket=config.KB_BUCKET,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    print(f"Bucket {config.KB_BUCKET} created")


def _sync(s3, local_dir, prefix: str, pattern: str) -> None:
    """Upload local files and delete objects under the prefix that no longer exist locally."""
    files = {prefix + p.name: p for p in sorted(local_dir.glob(pattern))}
    for key, path in files.items():
        s3.upload_file(str(path), config.KB_BUCKET, key)
        print(f"  uploaded s3://{config.KB_BUCKET}/{key}")
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=config.KB_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"] not in files:
                s3.delete_object(Bucket=config.KB_BUCKET, Key=obj["Key"])
                print(f"  deleted  s3://{config.KB_BUCKET}/{obj['Key']}")


def upload(s3) -> None:
    if not bucket_exists(s3):
        _create_bucket(s3)
    _sync(s3, KB_DIR, config.KB_PREFIX, "*.md")
    _sync(s3, EVALS_DIR, EVALS_PREFIX, "*.jsonl")
    print("Done. Re-index the knowledge base: agentcore run ingest")


def delete(s3) -> None:
    if not bucket_exists(s3):
        print(f"Bucket {config.KB_BUCKET} not found, skipping")
        return
    bucket = boto3.resource("s3", region_name=config.REGION).Bucket(config.KB_BUCKET)
    bucket.objects.all().delete()
    bucket.delete()
    print(f"Bucket {config.KB_BUCKET} deleted")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    s3 = boto3.client("s3", region_name=config.REGION)
    match command:
        case "upload":
            upload(s3)
        case "delete":
            delete(s3)
        case _:
            sys.exit(USAGE)
