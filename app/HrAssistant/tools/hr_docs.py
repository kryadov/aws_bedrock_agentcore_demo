"""search_hr_docs: semantic search over the HR handbook Knowledge Base.

The CLI creates the KB (agentcore.json -> knowledgeBases) but does not pass its id to the
runtime, so the tool looks it up once by physical name `<project>_<kb>`.
"""

import os
from functools import cache

import boto3
from strands import tool

KNOWLEDGE_BASE_NAME = os.getenv("KNOWLEDGE_BASE_NAME", "IcAgentcoreDemo_HrDocs")
REGION = os.getenv("AWS_REGION")
TOP_K = 5

# Marks each chunk in the tool result; main.py splits on it to get grounding sources.
CHUNK_SEPARATOR = "\n\n---\n\n"


@cache
def _knowledge_base_id() -> str:
    client = boto3.client("bedrock-agent", region_name=REGION)
    for page in client.get_paginator("list_knowledge_bases").paginate():
        for kb in page["knowledgeBaseSummaries"]:
            if kb["name"] == KNOWLEDGE_BASE_NAME:
                return kb["knowledgeBaseId"]
    raise RuntimeError(f"Knowledge base {KNOWLEDGE_BASE_NAME} not found; run `agentcore deploy`")


@tool
def search_hr_docs(query: str) -> str:
    """Search the Quibblefrost Widgetworks HR handbook (leave, travel, expenses, remote work, equipment,
    benefits, conduct, onboarding, confidential HR notes). Always call it before answering an
    HR question and answer only from what it returns.

    Args:
        query: the question rephrased as a short English search query
    """
    response = boto3.client("bedrock-agent-runtime", region_name=REGION).retrieve(
        knowledgeBaseId=_knowledge_base_id(),
        retrievalQuery={"text": query},
        # the CLI creates a managed KB: it takes managedSearchConfiguration, vectorSearchConfiguration is rejected
        retrievalConfiguration={"managedSearchConfiguration": {"numberOfResults": TOP_K}},
    )
    chunks = []
    for result in response["retrievalResults"]:
        source = result.get("location", {}).get("s3Location", {}).get("uri", "unknown")
        chunks.append(f"[source: {source.rsplit('/', 1)[-1]}]\n{result['content']['text']}")
    return CHUNK_SEPARATOR.join(chunks) or "Nothing found in the HR handbook."
