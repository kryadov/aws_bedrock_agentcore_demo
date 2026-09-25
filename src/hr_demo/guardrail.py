"""Create/update or delete the HR guardrail: `uv run guardrail create|delete`.

The agentcore CLI has no command for Bedrock Guardrails (its `add policy` guards Gateway tool
calls, not model text), so this script owns the guardrail and writes GUARDRAIL_ID/VERSION into
agentcore.json -> runtimes[].envVars. The next `agentcore deploy` passes them to the runtime.

Every policy below is tailored to data/kb (see 07-employee-directory.md).
"""

import sys
import time

import boto3

from hr_demo import config, project

USAGE = "usage: uv run guardrail create|delete"

# Standard tier: better accuracy, multilingual (the user may ask in Russian); needs cross-region inference.
STANDARD = {"tierName": "STANDARD"}
CROSS_REGION_PROFILE = "us.guardrail.v1:0"

# 1. General content filters + prompt attacks (prompt attacks only make sense on input)
CONTENT_FILTERS = [
    {"type": t, "inputStrength": "HIGH", "outputStrength": "HIGH"}
    for t in ("HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT")
] + [{"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"}]

# 2. Domain denied topics: what the directory extract contains but must never leave HR
DENIED_TOPICS = [
    {
        "name": "ColleagueCompensation",
        "definition": "Salary, bonus, equity, bank details or other compensation of another employee, "
        "named or referred to as he, she or they.",
        "examples": ["What is Ben Carter's salary?", "How much does Dana earn?", "Show me Alice's bank account.",
                     "What's his salary?", "How much does she make?"],
        "type": "DENY",
    },
    {
        "name": "EmployeePerformanceAndLayoffs",
        "definition": "Performance reviews, improvement plans, disciplinary cases, planned layoffs, terminations "
        "or restructuring affecting specific employees or teams.",
        "examples": ["Is Chen Wei on a PIP?", "Who will be laid off in Customer Success?", "Is my team being restructured?"],
        "type": "DENY",
    },
    {
        "name": "IndividualLegalAdvice",
        "definition": "Requests for personal legal advice: whether to sue, how to win a dispute with the employer, "
        "interpretation of an individual contract or immigration case.",
        "examples": ["Should I sue the company?", "Can I win a wrongful termination case?"],
        "type": "DENY",
    },
]

# 3. Words: the confidential codename + managed profanity list
WORDS = {"wordsConfig": [{"text": "Project Falcon"}], "managedWordListsConfig": [{"type": "PROFANITY"}]}

# 4. PII. Masking runs on output only: masked *input* would come back as "intervened" and the agent
#    treats input interventions as blocks. Hard identifiers are blocked both ways.
# "77 Quibble Court, New York, NY 10019" / "ul. Zmyślona 7, 00-850 Warszawa"; a bare city stays visible
HOME_ADDRESS = (
    r"(?:\d{1,5} [A-Z][a-z]+ (?:Way|Road|Rd|Court|Ct|Lane|Ln|Street|St|Avenue|Ave|Boulevard|Blvd)|ul\. [^,\s]+ \d+)"
    r"(?:, [A-Za-z ]+, [A-Z]{2} \d{5}|, \d{2}-\d{3} [^\s,]+)?"
)
_OUTPUT_ONLY = {"inputAction": "NONE", "inputEnabled": False, "outputAction": "ANONYMIZE", "outputEnabled": True}
PII = {
    "piiEntitiesConfig": [
        *[
            {"type": t, "action": "BLOCK"}
            for t in (
                "US_SOCIAL_SECURITY_NUMBER",
                "US_BANK_ACCOUNT_NUMBER",
                "US_BANK_ROUTING_NUMBER",
                "INTERNATIONAL_BANK_ACCOUNT_NUMBER",
                "CREDIT_DEBIT_CARD_NUMBER",
            )
        ],
    ],
    # Regexes instead of the generic ADDRESS/EMAIL/PHONE entities so that office cities ("New York")
    # and public contact points (hr@..., it-help@..., the 1-800 hotline) stay visible while
    # personal ones get masked.
    "regexesConfig": [
        {"name": "EmployeeId", "description": "Workday employee id", "pattern": r"EMP-\d{5}", "action": "ANONYMIZE", **_OUTPUT_ONLY},
        {"name": "PersonalEmail", "description": "first.last@ work email", "pattern": r"[a-z]+\.[a-z]+@quibblefrost\.example", "action": "ANONYMIZE", **_OUTPUT_ONLY},
        {"name": "HomeAddress", "description": "street address, optionally with city and zip", "pattern": HOME_ADDRESS, "action": "ANONYMIZE", **_OUTPUT_ONLY},
        {"name": "PersonalPhone", "description": "direct phone numbers", "pattern": r"\+\d{1,3} \d{2,3} 555 \d{4}", "action": "ANONYMIZE", **_OUTPUT_ONLY},
    ],
}

# 5. Contextual grounding: the answer must be supported by the retrieved chunks and relevant to the question
GROUNDING = {
    "filtersConfig": [
        {"type": "GROUNDING", "threshold": 0.75},
        {"type": "RELEVANCE", "threshold": 0.5},
    ]
}


def _policies() -> dict:
    return dict(
        name=config.GUARDRAIL_NAME,
        description="HrAssistant: HR topics, PII masking, grounding, content filters",
        contentPolicyConfig={"filtersConfig": CONTENT_FILTERS, "tierConfig": STANDARD},
        topicPolicyConfig={"topicsConfig": DENIED_TOPICS, "tierConfig": STANDARD},
        wordPolicyConfig=WORDS,
        sensitiveInformationPolicyConfig=PII,
        contextualGroundingPolicyConfig=GROUNDING,
        crossRegionConfig={"guardrailProfileIdentifier": CROSS_REGION_PROFILE},
        blockedInputMessaging=config.GUARDRAIL_BLOCKED_INPUT,
        blockedOutputsMessaging=config.GUARDRAIL_BLOCKED_OUTPUT,
    )


def find_id(client) -> str | None:
    for page in client.get_paginator("list_guardrails").paginate():
        for g in page["guardrails"]:
            if g["name"] == config.GUARDRAIL_NAME:
                return g["id"]
    return None


def _wait_ready(client, guardrail_id: str) -> None:
    while (status := client.get_guardrail(guardrailIdentifier=guardrail_id)["status"]) != "READY":
        if status == "FAILED":
            sys.exit(f"Guardrail {guardrail_id} is FAILED")
        time.sleep(2)


def create(client) -> None:
    guardrail_id = find_id(client)
    if guardrail_id:
        client.update_guardrail(guardrailIdentifier=guardrail_id, **_policies())
        print(f"Guardrail {config.GUARDRAIL_NAME} updated: {guardrail_id}")
    else:
        guardrail_id = client.create_guardrail(**_policies())["guardrailId"]
        print(f"Guardrail {config.GUARDRAIL_NAME} created: {guardrail_id}")
    _wait_ready(client, guardrail_id)

    # DRAFT is mutable; the runtime uses an immutable numbered version
    version = client.create_guardrail_version(guardrailIdentifier=guardrail_id)["version"]
    project.set_runtime_env({"GUARDRAIL_ID": guardrail_id, "GUARDRAIL_VERSION": version})
    print(f"Version {version} written to agentcore/agentcore.json. Apply with `agentcore deploy`.")


def delete(client) -> None:
    guardrail_id = find_id(client)
    if guardrail_id:
        client.delete_guardrail(guardrailIdentifier=guardrail_id)
        print(f"Guardrail {guardrail_id} deleted")
    else:
        print(f"Guardrail {config.GUARDRAIL_NAME} not found, skipping")
    project.set_runtime_env({"GUARDRAIL_ID": "", "GUARDRAIL_VERSION": ""})
    print("Guardrail env vars cleared in agentcore/agentcore.json")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    client = boto3.client("bedrock", region_name=config.REGION)
    match command:
        case "create":
            create(client)
        case "delete":
            delete(client)
        case _:
            sys.exit(USAGE)
