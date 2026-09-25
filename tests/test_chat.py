"""uv run chat: response decoding and session ids (no AWS)."""

import io
import json

from hr_demo import chat


class _Body(io.BytesIO):
    def iter_lines(self):
        return iter(self.getvalue().splitlines())


def test_events_from_sse_stream():
    lines = [b'data: {"guardrail": "OUTPUT"}', b"", b'data: {"event": {"contentBlockDelta": {"delta": {"text": "hi"}}}}']
    response = {"contentType": "text/event-stream", "response": _Body(b"\n".join(lines))}
    assert list(chat._events(response)) == [
        {"guardrail": "OUTPUT"},
        {"event": {"contentBlockDelta": {"delta": {"text": "hi"}}}},
    ]


def test_events_from_json_body():
    response = {"contentType": "application/json", "response": _Body(json.dumps({"result": "ok"}).encode())}
    assert list(chat._events(response)) == [{"result": "ok"}]


def test_session_ids_are_long_enough_and_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "SESSIONS_FILE", tmp_path / "sessions.json")
    first = chat.session_for("alice")
    assert len(first) >= 33 and first.startswith("alice-")
    assert chat.session_for("alice") == first
    assert chat.session_for("alice", new=True) != first


def test_user_names():
    assert chat.USER_RE.fullmatch("alice_01")
    assert not chat.USER_RE.fullmatch("Alice Moreno")


def test_replay_prints_recorded_answers_in_order(tmp_path, monkeypatch, capsys):
    session = {"runtime": "rt", "session": "s", "turns": [
        {"question": "q1", "answer": ["Hello ", "table"], "seconds": 9.0},
        {"question": "q2", "answer": ["  [guardrail: input] ", "blocked"], "seconds": 1.0},
    ]}
    path = tmp_path / "session.json"
    path.write_text(json.dumps(session), encoding="utf-8")
    monkeypatch.setattr(chat.time, "sleep", lambda s: None)
    replay = chat.Replay(str(path))
    replay.ask("q1")
    replay.ask("q2")
    replay.ask("q3")
    assert capsys.readouterr().out == "Hello table\n\n  [guardrail: input] blocked\n\n  (no more recorded turns)\n\n"


def test_align_tables_pads_columns_and_keeps_other_text():
    text = "Teams:\n\n| Team | Name |\n|---|---|\n| Data Platform | Alice |\n| Executive | Dana Whitfield |\n\nDone."
    assert chat.align_tables(text) == (
        "Teams:\n\n"
        "| Team          | Name           |\n"
        "|---------------|----------------|\n"
        "| Data Platform | Alice          |\n"
        "| Executive     | Dana Whitfield |\n"
        "\nDone."
    )
    assert chat.align_tables("| a | b |\n| c |") == "| a | b |\n| c |"  # ragged: unchanged
