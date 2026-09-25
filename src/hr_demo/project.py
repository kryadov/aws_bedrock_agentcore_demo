"""Read and patch the CLI-owned files in agentcore/ (agentcore.json, aws-targets.json, .cli/deployed-state.json)."""

import json
from typing import Any

from hr_demo import config

SPEC_FILE = config.AGENTCORE_DIR / "agentcore.json"
TARGETS_FILE = config.AGENTCORE_DIR / "aws-targets.json"
STATE_FILE = config.AGENTCORE_DIR / ".cli" / "deployed-state.json"


def set_runtime_env(values: dict[str, str]) -> None:
    """Upsert runtimes[RUNTIME_NAME].envVars; applied to AWS by the next `agentcore deploy`."""
    spec = json.loads(SPEC_FILE.read_text(encoding="utf-8"))
    runtime = next((r for r in spec["runtimes"] if r["name"] == config.RUNTIME_NAME), None)
    if runtime is None:
        raise RuntimeError(f"runtime {config.RUNTIME_NAME} not found in {SPEC_FILE}")
    env = {e["name"]: e["value"] for e in runtime.get("envVars", [])}
    env.update(values)
    runtime["envVars"] = [{"name": k, "value": v} for k, v in env.items()]
    SPEC_FILE.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")


def set_account(account: str) -> None:
    """Point the deploy target and the KB data source at `account`.

    `uv run stack up` sets the real account, `down` puts back config.PLACEHOLDER_ACCOUNT, so the
    committed files carry no account id.
    """
    targets = json.loads(TARGETS_FILE.read_text(encoding="utf-8"))
    for target in targets:
        target["account"] = account
        target["region"] = config.REGION
    TARGETS_FILE.write_text(json.dumps(targets, indent=2) + "\n", encoding="utf-8")

    spec = json.loads(SPEC_FILE.read_text(encoding="utf-8"))
    uri = f"s3://{config.kb_bucket(account)}/{config.KB_PREFIX}"
    for kb in spec.get("knowledgeBases", []):
        for source in kb.get("dataSources", []):
            if source.get("type") == "S3":
                source["uri"] = uri
    SPEC_FILE.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")


def _find_arns(node: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    if isinstance(node, dict):
        found = []
        for key, value in node.items():
            if key == "runtimeArn" and isinstance(value, str):
                found.append((path, value))
            else:
                found += _find_arns(value, path + (key,))
        return found
    return []


def runtime_arn() -> str:
    """RUNTIME_ARN from .env, else the ARN `agentcore deploy` recorded for RUNTIME_NAME."""
    if config.RUNTIME_ARN:
        return config.RUNTIME_ARN
    if STATE_FILE.exists():
        arns = _find_arns(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        named = [arn for path, arn in arns if config.RUNTIME_NAME in path]
        if named or len(arns) == 1:
            return (named or [arns[0][1]])[0]
    raise RuntimeError("runtime ARN not found: run `agentcore deploy` or set RUNTIME_ARN in .env")
