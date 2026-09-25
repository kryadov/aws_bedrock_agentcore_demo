# RESEARCH.md

Design notes and options that were studied but are not (yet) part of the project.

## Multi Agents approach (not implemented)

The project stays a **single agent**. This section records how a multi-agent version would look.

### How routing works today

There is no router code. The model (Claude via Strands) picks the next tool on every step from the
system prompt, the tool descriptions and the conversation history.

```
question ─► guardrail INPUT ─► Strands Agent (one, Claude Sonnet)
                                 │ the model sees: SYSTEM_PROMPT
                                 │   + tool descriptions:
                                 │     search_hr_docs          (docstring in tools/hr_docs.py)
                                 │     list/search_employees,  (toolDefinitions in agentcore.json,
                                 │     get_direct_reports       listed over MCP by the Gateway)
                                 ▼
                      ┌─► model emits toolUse(search_employees, {...}) ──┐
                      │                                                  ▼
                      │                     Strands runs the tool ─► toolResult
                      └──────── model thinks again ◄─────────────────────┘
                                 │ enough data → text answer
                                 ▼
                      guardrail OUTPUT (grounding vs all toolResults) ─► answer
```

Routing levers: the rules in `SYSTEM_PROMPT`, each tool's `description`, and the history (a follow-up
like "who of them is in New York?" may be answered from the previous turn without any tool call).

### How AgentCore handles multiple agents

AgentCore is framework-agnostic: Runtime hosts any agent code. Multi-agent behavior comes from the
framework (Strands) or from several runtimes talking to each other.

```
A) Agents as tools, one Runtime                  B) Separate Runtimes over A2A
┌──────── Runtime HrAssistant ────────┐        Runtime Orchestrator (HTTP)
│ Orchestrator (Agent)                │           │ InvokeAgentRuntime / A2A client (SigV4)
│  ├─ @tool ask_directory ─► Agent DB │           ├──► Runtime DirectoryAgent (protocol: A2A)
│  │        (Gateway MCP tools)       │           │      └─ Gateway → Lambda → RDS
│  └─ @tool ask_handbook ─► Agent RAG │           └──► Runtime HandbookAgent (protocol: A2A)
│           (search_hr_docs)          │                  └─ KB Retrieve
└─────────────────────────────────────┘        own deploy, IAM role, logs and traces each

C) Strands Swarm / Graph in one Runtime (strands.multiagent: swarm, graph, a2a)
   Swarm: agents hand off to each other on their own
   Graph: fixed graph  router ─► db | rag ─► composer
```

- The CLI schema supports it: a runtime has `protocol: 'HTTP' | 'MCP' | 'A2A' | 'AGUI'`
  (`agentcore/.llm-context/agentcore.ts`), so option B is `agentcore add agent` + `protocol` in
  `agentcore.json`, no CDK changes.
- Strands ships `strands.multiagent.a2a`, `swarm` and `graph`.

### Three-agent design (orchestrator + directory + handbook)

```
                 ┌──────────── Orchestrator ─────────────────┐
question ─► guard ─► understands intent, picks sub-agents      │
                 │   ├─► DirectoryAgent: "Data Platform staff" ─► rows from RDS
                 │   └─► HandbookAgent:  "Remote Work rules"   ─► KB chunks + source
                 │ composes the final answer; owns Memory      │
                 └─────────────────────────────────────────────┘ ─► guard OUTPUT ─► answer
```

| | Today (1 agent) | 3 agents |
|---|---|---|
| Who routes | the model picks a tool | the orchestrator's model picks a sub-agent (same mechanism, one level up) |
| LLM calls per question | 2–3 | 4–6 (noticeably slower and more expensive) |
| Guardrail grounding | against raw chunks and rows | the orchestrator sees sub-agent summaries; raw results must be passed up, or grounding weakens |
| Memory | one | orchestrator only; sub-agents stateless |
| Demonstrates | Gateway, KB, guardrails | plus A2A, separate runtimes, per-agent IAM, distributed traces |

### Verdict

Functionally one agent is better here: each sub-agent would wrap a single tool. As a learning
exercise, option B (separate runtimes + A2A) is the one that shows AgentCore's own multi-agent
features. Implementation sketch:

```
agentcore add agent DirectoryAgent  (protocol A2A)  ─ Gateway tools (RDS)
agentcore add agent HandbookAgent   (protocol A2A)  ─ search_hr_docs (KB)
HrAssistant → Orchestrator: guardrails + Memory + calls to both agents
+ pass sub-agents' raw toolResults up so grounding keeps working
+ tests and HrScenarios trajectories (orchestrator → directory → handbook)
```

Option A takes about an hour, but in AWS it still looks like a single agent.
