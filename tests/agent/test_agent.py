"""HrAssistant runtime code, run in the agent's venv (see tests/conftest.py). No AWS calls."""

import os
import sys

from conftest import PROJECT_ROOT

os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.pop("AGENTCORE_GATEWAY_HRGATEWAY_URL", None)
sys.path.insert(0, str(PROJECT_ROOT / "app/HrAssistant"))

import httpx  # noqa: E402

import main  # noqa: E402
from tools import employee_directory  # noqa: E402
from tools.hr_docs import CHUNK_SEPARATOR  # noqa: E402


def test_grounding_sources_use_every_tool_result():
    messages = [
        {"role": "user", "content": [{"text": "q"}]},
        {"role": "user", "content": [
            {"toolResult": {"content": [{"text": f"leave policy{CHUNK_SEPARATOR}remote work"}]}},
            {"toolResult": {"content": [{"text": '{"employees": [{"full_name": "Chen Wei", "manager": null}]}'}]}},
            {"toolResult": {"content": [{"text": '{"error": "manager_name is required"}'}]}},
        ]},
        {"role": "assistant", "content": [{"text": "answer"}]},
    ]
    assert main._grounding_sources(messages) == [
        "leave policy",
        "remote work",
        "| full_name | manager |\n|---|---|\n| Chen Wei |  |",  # rows as Markdown: grounding scores JSON poorly
        '{"error": "manager_name is required"}',
    ]


def test_strip_trailing_tool_use():
    messages = [
        {"role": "user", "content": [{"text": "q"}]},
        {"role": "assistant", "content": [{"text": "let me check"}, {"toolUse": {"name": "x"}}]},
    ]
    assert main.strip_trailing_tool_use(messages)[-1] == {"role": "assistant", "content": [{"text": "let me check"}]}


def test_no_gateway_means_no_mcp_client():
    assert employee_directory.employee_directory_client() is None


def test_gateway_requests_are_sigv4_signed(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    auth = employee_directory._SigV4("us-west-2")
    request = httpx.Request("POST", "https://gw.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp", content=b"{}")
    signed = next(auth.auth_flow(request))
    assert signed.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-west-2/bedrock-agentcore/aws4_request" in signed.headers["Authorization"]
    assert "x-amz-date" in signed.headers
