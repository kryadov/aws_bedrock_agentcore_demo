"""Names and data that must agree across files the CLI, the CDK and our scripts read separately."""

import json
import re

from conftest import PROJECT_ROOT
from test_employee_directory import handler

from hr_demo import config, guardrail, hr_db

AGENTCORE = json.loads((PROJECT_ROOT / "agentcore/agentcore.json").read_text(encoding="utf-8"))
TARGET = next(t for g in AGENTCORE["agentCoreGateways"] for t in g["targets"] if t["name"] == "EmployeeDirectory")
SEED = (PROJECT_ROOT / "data/db/seed.sql").read_text(encoding="utf-8")


def test_tool_definitions_match_the_lambda():
    tools = {t["name"]: t["inputSchema"] for t in TARGET["toolDefinitions"]}
    assert set(tools) == set(handler.QUERIES)
    for name, schema in tools.items():
        argument = handler.ARGUMENT[name]
        assert argument in schema["properties"]
        assert (argument in schema.get("required", [])) == (name not in handler.OPTIONAL)


def test_lambda_names_match_env_example():
    # the CLI can't give the Lambda env vars, so its constants duplicate .env.example
    assert handler.CLUSTER_NAME == config.DB_CLUSTER
    assert handler.DATABASE == config.DB_NAME
    assert handler.READER_SECRET == config.DB_READER_SECRET


def test_lambda_policy_covers_cluster_and_reader_secret():
    resources = json.dumps(TARGET["compute"]["iamPolicy"])
    assert f":cluster:{config.DB_CLUSTER}\"" in resources
    assert f":secret:{config.DB_READER_SECRET}-*\"" in resources
    assert "rds-data:ExecuteStatement" in resources


def test_sql_files_split_into_statements():
    schema = hr_db._statements(PROJECT_ROOT / "data/db/schema.sql")
    seed = hr_db._statements(PROJECT_ROOT / "data/db/seed.sql")
    assert [s.split()[0] for s in schema] == ["DROP", "DROP", "CREATE", "CREATE", "CREATE", "CREATE"]
    assert [s.split()[0] for s in seed] == ["INSERT", "INSERT"]
    assert not any(s.startswith("--") for s in schema + seed)


def _regex(name: str) -> re.Pattern:
    return re.compile(next(r["pattern"] for r in guardrail.PII["regexesConfig"] if r["name"] == name))


def test_guardrail_regexes_mask_every_seeded_contact():
    emails = re.findall(r"'([\w.]+@[\w.]+)'", SEED)
    phones = re.findall(r"'(\+[\d ]+)'", SEED)
    ids = re.findall(r"'(EMP-\d+)'", SEED)
    assert len(emails) == len(phones) == 5
    assert all(_regex("PersonalEmail").fullmatch(e) for e in emails)
    assert all(_regex("PersonalPhone").fullmatch(p) for p in phones)
    assert all(_regex("EmployeeId").fullmatch(i) for i in ids)


def test_home_addresses_masked_but_cities_visible():
    addresses = re.findall(r"'(\d+ [^']+, [A-Z]{2} \d{5}|ul\. [^']+)'", SEED)
    assert len(addresses) == 5
    assert all(_regex("HomeAddress").fullmatch(a) for a in addresses)
    table = "| Chen Wei | Customer Success | New York |\n| Dana Whitfield | Executive | San Francisco |"
    assert not _regex("HomeAddress").search(table)


def test_public_contacts_stay_visible():
    kb = "\n".join(p.read_text(encoding="utf-8") for p in (PROJECT_ROOT / "data/kb").glob("*.md"))
    public = set(re.findall(r"[\w.-]+@quibblefrost\.example", kb))
    assert public and not any(_regex("PersonalEmail").fullmatch(e) for e in public)


def test_seeded_ssns_are_advertising_range():
    assert set(re.findall(r"'(\d{3}-\d{2}-\d{4})'", SEED)) <= {f"987-65-432{i}" for i in range(10)}


def test_directory_data_lives_only_in_the_database():
    notes = (PROJECT_ROOT / "data/kb/07-employee-directory.md").read_text(encoding="utf-8")
    assert not re.search(r"EMP-\d{5}|987-65-|\$\d{3},\d{3}", notes)


def test_salary_topic_covers_pronoun_questions():
    topic = next(t for t in guardrail.DENIED_TOPICS if t["name"] == "ColleagueCompensation")
    assert len(topic["examples"]) <= 5
    assert "What's his salary?" in topic["examples"]


def test_no_account_id_in_the_repo():
    # public repo: CLI files carry the placeholder at rest, IAM ARNs use "*", KB_BUCKET uses {account}
    files = [p for pattern in ("*.md", "*.json", "*.py", "*.sh", "*.tape", ".env.example")
             for p in PROJECT_ROOT.rglob(pattern)
             if not {".venv", ".cache", "node_modules", "cdk.out", ".cli", "data"} & set(p.relative_to(PROJECT_ROOT).parts)]
    for path in files:
        ids = set(re.findall(r"(?<![\w-])\d{12}(?![\w-])", path.read_text(encoding="utf-8"))) - {config.PLACEHOLDER_ACCOUNT}
        assert not ids, f"{path.relative_to(PROJECT_ROOT)}: {ids}"


def test_set_account_round_trip(tmp_path, monkeypatch):
    from hr_demo import project

    for name in ("SPEC_FILE", "TARGETS_FILE"):
        original = getattr(project, name)
        copy = tmp_path / original.name
        copy.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.setattr(project, name, copy)
    account = "1" * 12  # built, not written out: this file is scanned by the test above
    project.set_account(account)
    spec, targets = (json.loads(getattr(project, n).read_text(encoding="utf-8")) for n in ("SPEC_FILE", "TARGETS_FILE"))
    assert targets[0]["account"] == account
    assert spec["knowledgeBases"][0]["dataSources"][0]["uri"] == f"s3://{config.kb_bucket(account)}/{config.KB_PREFIX}"
    project.set_account(config.PLACEHOLDER_ACCOUNT)
    assert json.loads(project.SPEC_FILE.read_text(encoding="utf-8")) == AGENTCORE
