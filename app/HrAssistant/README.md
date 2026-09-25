# HrAssistant

The agent deployed to AgentCore Runtime (CodeZip). Scaffolded by `agentcore create`, then
customized; see the project [README](../../README.md) and [AGENTS.md](../../AGENTS.md).

| File | What |
|---|---|
| `main.py` | `@app.entrypoint`: guardrail on input → Strands agent → guardrail on output |
| `model/load.py` | `BedrockModel`, `MODEL_ID` env var |
| `memory/session.py` | AgentCore Memory session manager; LTM retrieval per namespace |
| `tools/hr_docs.py` | `search_hr_docs`: Retrieve from the HR Knowledge Base |
| `safety/guardrail.py` | `ApplyGuardrail` INPUT / OUTPUT with contextual grounding |

Environment variables (set in `agentcore/agentcore.json` → `runtimes[].envVars`, memory id
injected by the CLI): `MODEL_ID`, `KNOWLEDGE_BASE_NAME`, `GUARDRAIL_ID`, `GUARDRAIL_VERSION`,
`MEMORY_HRASSISTANTMEMORY_ID`.

Local run: `uv sync`, then `agentcore dev` from the project root (deploys resources first unless
`--skip-deploy`).
