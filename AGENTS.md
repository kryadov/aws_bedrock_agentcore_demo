# AGENTS.md — aws_bedrock_agentcore_demo

Context for AI coding assistants. Human docs: [README.md](README.md) / [README_ru.md](README_ru.md).

Internal HR assistant (fictional company Quibblefrost Widgetworks) on Amazon Bedrock AgentCore, built with the
`agentcore` CLI 0.30.0: Runtime + Memory (STM + 4 LTM strategies) + Knowledge Base + Gateway (MCP,
AWS_IAM) with a Lambda target over Aurora PostgreSQL (RDS Data API) + Bedrock Guardrails +
AgentCore Evaluations + Bedrock RAG evaluation.

## Project rules

1. **Language: Python 3, tooling: uv.** Two uv projects:
   - root `pyproject.toml` (`hr-demo`, `src/hr_demo/`): operator scripts and the CLI client;
   - `app/HrAssistant/pyproject.toml`: the agent, deployed as CodeZip;
   - `app/EmployeeDirectory/pyproject.toml`: the gateway Lambda (boto3 only), bundled by the CDK.
   Run everything with `uv run …` / `uv sync`; never pip.
2. **English** for code, comments, UI strings, `README.md`, `AGENTS.md`. `README_ru.md` is the
   Russian copy of `README.md` — keep both in sync on every change.
3. **The CDK app in `agentcore/cdk/` is generated TypeScript — never edit it.** The CLI has no
   Python CDK. Infrastructure changes go through `agentcore add|remove` or `agentcore/agentcore.json`
   (`envVars`, `additionalPolicies`, …). What the CLI can't create lives in Python scripts under
   `src/hr_demo/` (guardrail, S3 bucket, RAG eval job, RDS database). The Lambda gateway target is
   hand-written in `agentcore.json` (`add gateway-target` creates Lambda tools only in the TUI).
4. **No AWS writes without explicit user consent**: `agentcore deploy`, `agentcore dev` (it deploys
   first unless `--skip-deploy`), `run ingest`, `run eval`, `uv run stack up|down` (`up.*`/`down.*`),
   `uv run guardrail|kb-data|rag-eval|hr-db` (except `hr-db status`).
   Read-only calls and `agentcore validate` / `package` are fine.
5. **The target account (the one of your AWS credentials) may run other workloads.**
   Never touch existing resources. Prefix everything: `IcAgentcoreDemo…` (CLI resources),
   `ic-agentcore-demo-…` (script resources). The repo is public: never commit an account id —
   `agentcore/*.json` hold the placeholder `123456789012` (`uv run stack up` sets the real one,
   `down` restores it), IAM ARNs use `*` for the account, `KB_BUCKET` uses `{account}`.
6. **Fully internal project**: no external MCP servers or internet tools (the template's
   `mcp.exa.ai` client was removed on purpose). The AgentCore Gateway `HrGateway` is internal
   (AWS_IAM, only the runtime role may invoke it) and is the only MCP server.
7. Do not rename resources in `agentcore.json`: `name` is the CloudFormation logical id —
   renaming destroys and recreates the resource (and its data: memory, KB index).

## Wiring the CLI doesn't do (and how it's done here)

```
agentcore.json runtimes[HrAssistant]
  envVars:
    MODEL_ID              us.anthropic.claude-sonnet-5          → model/load.py
    KNOWLEDGE_BASE_NAME   IcAgentcoreDemo_HrDocs (<project>_<kb>) → tools/hr_docs.py (id via ListKnowledgeBases)
    GUARDRAIL_ID/VERSION  written by `uv run guardrail create`   → safety/guardrail.py
    MEMORY_HRASSISTANTMEMORY_ID   injected by the CLI itself     → memory/session.py
  additionalPolicies: ../../agentcore/policies/hr-assistant.json  (path relative to codeLocation)
    bedrock:ListKnowledgeBases, bedrock:Retrieve, bedrock:ApplyGuardrail (+ guardrail-profile)
  AGENTCORE_GATEWAY_HRGATEWAY_URL + InvokeGateway   injected by the CDK → tools/employee_directory.py
                                                   (Strands MCPClient, SigV4 httpx.Auth)

agentCoreGateways[HrGateway].targets[EmployeeDirectory]   (hand-written, targetType: lambda)
  toolDefinitions   list_employees(team?), search_employees(query), get_direct_reports(manager_name)
  compute           app/EmployeeDirectory/handler.py — no VPC, no env vars (CDK limits) →
                    RDS Data API + DB names hardcoded = .env.example DB_* (tests/test_consistency.py)
  iamPolicy         rds-data:ExecuteStatement on the cluster, GetSecretValue on the reader secret
Aurora ic-agentcore-demo-hr  created by `uv run hr-db create`; Lambda reads as role hr_mcp (SELECT only)
```

## Agent turn (app/HrAssistant/main.py)

```
prompt ─► ApplyGuardrail INPUT ─ intervened ─► yield {"guardrail":"INPUT"} + blocked text, return
             │                                 (agent not called → nothing stored in Memory)
             ▼
        agent.stream_async (text deltas buffered when guardrail is on)
             ▼
        ApplyGuardrail OUTPUT: guard_content=answer, grounding_source=all toolResult blocks of
                               this turn (KB chunks + directory rows as a Markdown table), query=prompt
             │ intervened ─► redact_latest_message in Memory + yield {"guardrail":"OUTPUT"}
             ▼
        yield {"event":{"contentBlockDelta":{"delta":{"text": …}}}}   ← agentcore invoke / uv run chat
```

- PII masking (ANONYMIZE) is output-only; input carries only blocking policies, so every input
  intervention is treated as a block. Keep it that way when editing `src/hr_demo/guardrail.py`.
- Guardrail policies are tailored to `data/db/seed.sql` (salaries, SSNs, bank accounts, email and
  phone formats) and `data/kb/07-employee-directory.md` (PIP, layoffs, codename "Project Falcon").
  The directory tools return sensitive columns on purpose — the guardrail is the demo's last line.
  Changing the data may require changing the policies; `uv run pytest` checks the regexes.
- Keep `strip_trailing_tool_use()` for caller-supplied message histories; prompts must be strings.

## Change recipes

| Change | Steps |
|---|---|
| Whole environment | `./up.sh` (idempotent create/update) · `./down.sh` (delete) · `uv run stack status` |
| Agent code | edit `app/HrAssistant/` → `agentcore deploy` |
| Employee data | edit `data/db/*.sql` → `uv run hr-db seed` |
| Directory tools | edit `app/EmployeeDirectory/handler.py` + `toolDefinitions` in `agentcore.json` → `uv run pytest` → `agentcore deploy` |
| Guardrail policies | edit `src/hr_demo/guardrail.py` → `uv run guardrail create` → `agentcore deploy` |
| HR documents | edit `data/kb/` → `uv run kb-data upload` → `agentcore run ingest` |
| Eval scenarios | edit `agentcore/datasets/HrScenarios.jsonl` → `agentcore run eval --dataset HrScenarios …` |
| RAG eval questions | edit `data/evals/rag-eval.jsonl` → `uv run kb-data upload` → `uv run rag-eval start` |
| Demo GIF | record: `uv run chat --record docs/demo/session.json` (deployed stack); render: `sh docs/demo/render.sh` (offline) |
| New CLI resource | `agentcore add <resource> …` → `agentcore validate` → document it in both READMEs |

## Layout

```
AGENTS.md  README.md  README_ru.md  RESEARCH.md (not implemented designs)  pyproject.toml  .env.example  up.sh|cmd  down.sh|cmd
src/hr_demo/        config, project (agentcore.json patcher), stack (up/down), chat, guardrail, kb_data, rag_eval, hr_db
data/kb/            HR handbook (English, fictional, confidential HR notes on purpose)
data/db/            schema.sql + seed.sql of the HR database (fictional PII and salaries on purpose)
data/evals/         RAG evaluation dataset
tests/              pytest, offline: `uv run pytest`; tests/agent: `cd app/HrAssistant && uv run --with pytest pytest ../../tests/agent`
docs/demo/          session.json (recorded answers) + chat.tape (VHS) → render.sh → chat.gif
agentcore/          CLI-owned: agentcore.json, aws-targets.json, policies/, datasets/, .llm-context/, cdk/
app/HrAssistant/    main.py, model/, memory/, tools/ (hr_docs, employee_directory), safety/
app/EmployeeDirectory/  handler.py: gateway Lambda target
```

## agentcore CLI reference

The `agentcore/` directory is a declarative model; `agentcore/cdk/` deploys it with the
`@aws/agentcore-cdk` L3 constructs.

- **Flat resource model**: runtimes, memories, knowledgeBases, evaluators, datasets, … are
  independent top-level arrays in `agentcore.json`. Tags flow to CloudFormation resources.
- **Schema first**: `agentcore/.llm-context/agentcore.ts` (`AgentCoreProjectSpec`) and
  `aws-targets.ts` document every field with `@regex/@min/@max`. Use exact enum literals,
  CloudFormation-safe names, then `agentcore validate`.
- **Removal**: `agentcore remove <resource>` then `agentcore deploy` to tear it down one resource.
  For the whole environment use `uv run stack down`: it deletes the CloudFormation stack
  `AgentCore-<projectName>-<target>` directly, because `agentcore remove all` empties agentcore.json.

| Command | Purpose |
|---|---|
| `agentcore validate` / `package` | check config / build the CodeZip locally (no AWS) |
| `agentcore deploy [--dry-run\|--diff]` | synthesize CDK and deploy |
| `agentcore status` / `logs` / `traces list` | deployed state, runtime logs, traces |
| `agentcore dev [--skip-deploy]` | local server with hot reload |
| `agentcore invoke [--user-id] [--session-id] [--stream] "…"` | call the runtime |
| `agentcore add knowledge-base\|evaluator\|dataset\|online-eval\|…` | add resources |
| `agentcore run ingest [--name]` | re-index KB data sources |
| `agentcore run eval --runtime … --evaluator … [--dataset\|--session-id] [-A …]` | on-demand evaluation |
| `agentcore run batch-evaluation` | evaluators across many sessions |
| `agentcore evals history` | past eval results |
| `agentcore dataset publish-version\|download` | dataset versions |

Evaluator placeholders: TRACE `{context} {assistant_turn} {expected_response}`; SESSION
`{assertions} {expected_tool_trajectory} {actual_tool_trajectory}`; TOOL_CALL `{context} {tool_turn}`.
Builtins: Coherence, Conciseness, Correctness, Faithfulness, GoalSuccessRate, Helpfulness, Insight,
InstructionFollowing, Refusal, ResponseRelevance, ToolSelectionAccuracy, …
