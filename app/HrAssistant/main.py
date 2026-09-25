import json
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager

from memory.session import get_memory_session_manager
from model.load import load_model
from safety import guardrail
from tools.employee_directory import employee_directory_client
from tools.hr_docs import CHUNK_SEPARATOR, search_hr_docs

app = BedrockAgentCoreApp()
log = app.logger

SYSTEM_PROMPT = """
You are the internal HR assistant of Quibblefrost Widgetworks.
- For any question about company policy, benefits, leave, travel, expenses, equipment,
  conduct or onboarding, call search_hr_docs first and answer only from its results.
  If the handbook has no answer, say so and point to hr@quibblefrost.example.
- For questions about people (who is who, teams, managers, titles, locations, hire dates,
  work contacts) use the employee directory tools (list_employees for an overview by team,
  search_employees, get_direct_reports). Show lists of people as a Markdown table.
  Combine both when a policy depends on the person, e.g. vacation days by tenure.
- Quote numbers (days, limits, amounts) exactly as in the handbook; name the source document.
- Never disclose another employee's salary, bank details, SSN, home address or performance data.
- No individual legal or tax advice: refer to HR or an external advisor.
- Use what you remember about the user (name, team, preferences) to personalize answers.
- Reply in the user's language, briefly.
"""

# MCP tools of the gateway are added only when it is deployed (not in a bare `agentcore dev`)
tools = [search_hr_docs, *filter(None, [employee_directory_client()])]


def agent_factory():
    cache = {}

    def get_or_create_agent(session_id, user_id):
        key = f"{session_id}/{user_id}"
        if key not in cache:
            cache[key] = Agent(
                model=load_model(),
                session_manager=get_memory_session_manager(session_id, user_id),
                conversation_manager=NullConversationManager(),
                system_prompt=SYSTEM_PROMPT,
                tools=tools,
            )
        return cache[key]

    return get_or_create_agent


get_or_create_agent = agent_factory()


def strip_trailing_tool_use(messages: Any) -> list[dict]:
    """Strip toolUse blocks from the tail until the last message has none."""
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")

    messages = list(messages)
    while messages:
        last = messages[-1]
        if not isinstance(last, dict):
            raise ValueError("each message must be an object")
        original_content = last.get("content", [])
        if not isinstance(original_content, list) or not all(isinstance(block, dict) for block in original_content):
            raise ValueError("each message content value must be a list of content blocks")

        content = [block for block in original_content if "toolUse" not in block]
        if len(content) == len(original_content):
            break
        if content:
            messages[-1] = {**last, "content": content}
            break
        messages.pop()

    return messages


def _extract_prompt(payload: dict):
    """Accept a caller-supplied message history or a plain prompt string."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if "messages" in payload:
        return strip_trailing_tool_use(payload["messages"])
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    return prompt


def _text_event(text: str) -> dict:
    """Converse-style stream event: rendered by `agentcore invoke` and by `uv run chat`."""
    return {"event": {"contentBlockDelta": {"delta": {"text": text}}}}


def _as_markdown(text: str) -> str:
    """Directory rows (JSON from the gateway) as a Markdown table, other results unchanged.

    The grounding check scores a Markdown answer against JSON rows very low (0.1-0.5 for a correct
    table of four people) and against the same rows as a Markdown table high (0.9+).
    """
    try:
        rows = json.loads(text).get("employees")
    except (ValueError, AttributeError):
        return text
    if not rows:
        return text
    columns = list(rows[0])
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join("" if row.get(c) is None else str(row[c]) for c in columns) + " |" for row in rows]
    return "\n".join(lines)


def _grounding_sources(messages: list[dict]) -> list[str]:
    """Tool results of this turn: KB chunks of search_hr_docs (split per chunk) and directory rows."""
    chunks = []
    for message in messages:
        for block in message.get("content", []):
            for item in block.get("toolResult", {}).get("content", []):
                if "text" in item:
                    chunks += [_as_markdown(c) for c in item["text"].split(CHUNK_SEPARATOR) if c.strip()]
    return chunks


def _redact_latest(agent: Agent, text: str) -> None:
    """Replace the stored answer so the unsafe original never reaches Memory or later turns."""
    agent.messages[-1]["content"] = [{"text": text}]
    if agent._session_manager:
        agent._session_manager.redact_latest_message(agent.messages[-1], agent)


@app.entrypoint
async def invoke(payload, context):
    session_id = getattr(context, "session_id", None) or "default-session"
    user_id = getattr(context, "user_id", None) or "default-user"
    prompt = _extract_prompt(payload)
    guarded = guardrail.enabled() and isinstance(prompt, str)

    # 1. Input check: a blocked question never reaches the model or Memory.
    if guarded:
        verdict = guardrail.check_input(prompt)
        if verdict.intervened:
            log.info("guardrail intervened on input")
            yield {"guardrail": "INPUT"}
            yield _text_event(verdict.text)
            return

    agent = get_or_create_agent(session_id, user_id)
    turn_start = len(agent.messages)

    # Without a guardrail stream tokens as they come; with it, buffer the answer for the output check.
    answer = ""
    async for event in agent.stream_async(prompt):
        if not isinstance(event, dict) or "event" not in event:
            continue
        delta = event["event"].get("contentBlockDelta", {}).get("delta", {})
        if "text" in delta:
            answer += delta["text"]
            if guarded:
                continue
        cbs = event["event"].get("contentBlockStart")
        if cbs is not None and not cbs.get("start"):
            continue
        yield event

    if not guarded:
        return

    # 2. Output check: grounding against this turn's tool results + PII masking.
    final = agent.messages[-1]
    answer = "".join(b.get("text", "") for b in final.get("content", [])) or answer
    verdict = guardrail.check_output(prompt, answer, _grounding_sources(agent.messages[turn_start:]))
    if verdict.intervened:
        log.info("guardrail intervened on output")
        _redact_latest(agent, verdict.text)
        yield {"guardrail": "OUTPUT"}
    yield _text_event(verdict.text)


if __name__ == "__main__":
    app.run()
