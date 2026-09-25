"""Explicit ApplyGuardrail calls around the agent turn.

Input:  the question alone -> denied topics, content filters, prompt attacks, PII.
Output: the answer as guard_content + retrieved chunks as grounding_source + question as
        query -> contextual grounding (hallucination / relevance) and PII masking.
"""

import json
import logging
import os
from typing import NamedTuple

import boto3

log = logging.getLogger("bedrock_agentcore.app")  # the app logger has the runtime's INFO handler
# Set in agentcore.json -> runtimes[].envVars by `uv run guardrail create`; empty = no guardrail
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "")
REGION = os.getenv("AWS_REGION")

_client = boto3.client("bedrock-runtime", region_name=REGION)


class Verdict(NamedTuple):
    intervened: bool
    text: str  # original text, the masked text or the blocked message


def enabled() -> bool:
    return bool(GUARDRAIL_ID and GUARDRAIL_VERSION)


def _apply(source: str, content: list[dict], original: str) -> Verdict:
    response = _client.apply_guardrail(
        guardrailIdentifier=GUARDRAIL_ID,
        guardrailVersion=GUARDRAIL_VERSION,
        source=source,
        content=content,
    )
    if response["action"] != "GUARDRAIL_INTERVENED":
        return Verdict(False, original)
    # which policy fired (topic, PII entity, grounding score …): visible in `agentcore logs`
    log.info("guardrail %s intervened: %s", source, json.dumps(response.get("assessments", []), default=str))
    return Verdict(True, "".join(o["text"] for o in response["outputs"]) or original)


def check_input(question: str) -> Verdict:
    return _apply("INPUT", [{"text": {"text": question}}], question)


def check_output(question: str, answer: str, sources: list[str]) -> Verdict:
    content = [{"text": {"text": answer, "qualifiers": ["guard_content"]}}]
    # Grounding needs both a source and a query; without retrieved chunks (small talk,
    # questions about the user's own memory) only the other policies apply.
    if sources:
        content += [{"text": {"text": s, "qualifiers": ["grounding_source"]}} for s in sources]
        content.append({"text": {"text": question, "qualifiers": ["query"]}})
    return _apply("OUTPUT", content, answer)
