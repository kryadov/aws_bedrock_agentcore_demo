"""CLI chat with the deployed HrAssistant runtime: `uv run chat`.

A thin client: every question is one InvokeAgentRuntime call. The runtime owns the agent,
Memory, Knowledge Base and guardrail; this loop only keeps (user -> session id) locally.

  uv run chat --record docs/demo/session.json   also save questions, answers and latencies
  uv run chat --replay docs/demo/session.json   answer from the saved file: no AWS, same output
                                                (the demo GIF is rendered from a replay)
"""

import json
import os
import re
import sys
import time
import uuid
from collections.abc import Iterator

import boto3
from botocore.exceptions import NoCredentialsError, TokenRetrievalError

from hr_demo import config, project

USAGE = "usage: uv run chat [--record FILE | --replay FILE]"
# replay pacing: seconds per streamed character, cap on the recorded "thinking" pause
REPLAY_CHAR_DELAY = float(os.environ.get("REPLAY_CHAR_DELAY", "0.004"))
REPLAY_MAX_WAIT = float(os.environ.get("REPLAY_MAX_WAIT", "2.5"))
HELP = "/reset - start a new session (long-term memory stays), /help - commands, /exit - quit"
USER_RE = re.compile(r"[a-z0-9_-]{1,64}")
SESSIONS_FILE = config.PROJECT_ROOT / ".sessions.json"


def ask_user() -> str:
    while True:
        user = input("User: ").strip().lower()
        if USER_RE.fullmatch(user):
            return user
        print("  use a-z, 0-9, _ or -, up to 64 chars")


def _load_sessions() -> dict[str, str]:
    return json.loads(SESSIONS_FILE.read_text(encoding="utf-8")) if SESSIONS_FILE.exists() else {}


def session_for(user: str, new: bool = False) -> str:
    """Session id per user, kept across restarts; runtime session ids need at least 33 chars."""
    sessions = _load_sessions()
    if new or user not in sessions:
        sessions[user] = f"{user}-{uuid.uuid4().hex}"
        SESSIONS_FILE.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
    return sessions[user]


def _events(response) -> Iterator[dict]:
    """Decode the runtime response: SSE `data: {...}` lines when streaming, one JSON body otherwise."""
    body = response["response"]
    if "text/event-stream" not in response.get("contentType", ""):
        yield json.loads(body.read())
        return
    for line in body.iter_lines():
        line = line.decode("utf-8")
        if line.startswith("data: "):
            yield json.loads(line[len("data: "):])


def align_tables(text: str) -> str:
    """Pad the cells of Markdown tables so the columns line up in a terminal."""
    lines, out, table = text.split("\n"), [], []

    def flush() -> None:
        rows = [[c.strip() for c in line.strip()[1:-1].split("|")] for line in table]
        if len({len(r) for r in rows}) != 1:  # ragged or cut-off table: leave as is
            out.extend(table)
        else:
            widths = [max(len(r[i]) for r in rows if not _is_rule(r)) for i in range(len(rows[0]))]
            for r in rows:
                cells = ["-" * w if _is_rule(r) else c.ljust(w) for c, w in zip(r, widths)]
                out.append(("|-" + "-|-".join(cells) + "-|") if _is_rule(r) else ("| " + " | ".join(cells) + " |"))
        table.clear()

    for line in lines:
        if line.strip().startswith("|") and line.strip().endswith("|") and len(line.strip()) > 1:
            table.append(line)
            continue
        if table:
            flush()
        out.append(line)
    if table:
        flush()
    return "\n".join(out)


def _is_rule(cells: list[str]) -> bool:
    return all(c and set(c) <= set("-: ") for c in cells)


def ask(client, arn: str, user: str, session: str, question: str) -> list[str]:
    """Print the answer as it streams; return the printed pieces (for --record)."""
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session,
        runtimeUserId=user,
        contentType="application/json",
        accept="application/json, text/event-stream",
        payload=json.dumps({"prompt": question}).encode(),
    )
    printed = []
    for event in _events(response):
        if not isinstance(event, dict):
            continue
        if "guardrail" in event:
            piece = f"  [guardrail: {event['guardrail'].lower()}] "
        else:
            piece = event.get("event", {}).get("contentBlockDelta", {}).get("delta", {}).get("text")
        if piece:
            print(align_tables(piece), end="", flush=True)
            printed.append(piece)  # recorded raw, aligned again on replay
    print("\n")
    return printed


class Replay:
    """Stands in for the runtime: answers the recorded turns in order."""

    def __init__(self, path: str):
        self.data = json.loads(open(path, encoding="utf-8").read())
        self.turns = iter(self.data["turns"])

    def ask(self, question: str) -> None:
        turn = next(self.turns, None)
        if turn is None:
            print("  (no more recorded turns)\n")
            return
        time.sleep(min(turn["seconds"], REPLAY_MAX_WAIT))
        for piece in turn["answer"]:
            for char in align_tables(piece):
                print(char, end="", flush=True)
                time.sleep(REPLAY_CHAR_DELAY)
        print("\n")


def _options() -> tuple[str, str]:
    args = sys.argv[1:]
    if not args:
        return "", ""
    if len(args) == 2 and args[0] in ("--record", "--replay"):
        return args[0], args[1]
    sys.exit(USAGE)


def main() -> None:
    mode, path = _options()
    try:
        user = ask_user()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    replay = Replay(path) if mode == "--replay" else None
    if replay:
        runtime_id, session = replay.data["runtime"], replay.data["session"]
    else:
        try:
            arn = project.runtime_arn()
        except RuntimeError as e:
            print(e)
            return
        client = boto3.client("bedrock-agentcore", region_name=config.REGION)
        session = session_for(user)
        # runtime id only: the full ARN carries the account id (keeps screenshots and demo GIFs clean)
        runtime_id = arn.rsplit("/", 1)[-1]
    recording = {"runtime": runtime_id, "session": session, "turns": []}

    print(f"Hi, {user}. Runtime: {runtime_id}\nSession: {session}\n{HELP}\n")

    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        match question:
            case "":
                continue
            case "/exit":
                break
            case "/help":
                print(f"  {HELP}\n")
                continue
            case "/reset":
                if not replay:
                    session = session_for(user, new=True)
                    recording.update(session=session, turns=[])
                print(f"  new session: {session}\n")
                continue

        if replay:
            replay.ask(question)
            continue
        try:
            started = time.monotonic()
            answer = ask(client, arn, user, session, question)
            recording["turns"].append({"question": question, "answer": answer,
                                       "seconds": round(time.monotonic() - started, 1)})
            if mode == "--record":
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(recording, f, indent=2, ensure_ascii=False)
        except (TokenRetrievalError, NoCredentialsError):
            print("  no AWS credentials or SSO token expired: run `aws sso login` and ask again\n")
        except Exception as e:
            print(f"  error: {type(e).__name__}: {e}\n")

    print("Bye. The conversation stays in AgentCore Memory.")
