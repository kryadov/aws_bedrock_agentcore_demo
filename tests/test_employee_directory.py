"""EmployeeDirectory Lambda: tool routing, arguments, Data API retries (boto3 stubbed, no AWS)."""

import importlib.util
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from conftest import PROJECT_ROOT

_spec = importlib.util.spec_from_file_location("employee_directory_handler", PROJECT_ROOT / "app/EmployeeDirectory/handler.py")
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)

FUNCTION_ARN = "arn:aws:lambda:us-west-2:123456789012:function:IcAgentcoreDemo-EmployeeDirectory"


def _context(tool: str):
    return SimpleNamespace(
        client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": f"EmployeeDirectory___{tool}"}),
        invoked_function_arn=FUNCTION_ARN,
    )


@pytest.fixture
def calls(monkeypatch):
    """Record _query calls instead of hitting the Data API."""
    recorded = []

    def fake_query(region, account, sql, value):
        recorded.append(SimpleNamespace(region=region, account=account, sql=sql, value=value))
        return [{"full_name": "Chen Wei"}]

    monkeypatch.setattr(handler, "_query", fake_query)
    return recorded


def test_search_routes_to_its_query(calls):
    result = handler.lambda_handler({"query": " Moreno "}, _context("search_employees"))
    assert result == {"employees": [{"full_name": "Chen Wei"}], "count": 1}
    assert calls[0].sql == handler.QUERIES["search_employees"]
    assert (calls[0].region, calls[0].account, calls[0].value) == ("us-west-2", "123456789012", "Moreno")


def test_list_employees_argument_is_optional(calls):
    handler.lambda_handler({}, _context("list_employees"))
    assert calls[0].value == ""  # → ILIKE '%%' = every non-confidential team


def test_list_employees_hides_confidential_teams():
    assert "NOT t.confidential" in handler.QUERIES["list_employees"]
    assert "confidential" not in handler.QUERIES["search_employees"]  # search still finds Project Falcon


@pytest.mark.parametrize("tool", ["search_employees", "get_direct_reports"])
def test_required_argument(calls, tool):
    assert handler.lambda_handler({}, _context(tool)) == {"error": f"argument {handler.ARGUMENT[tool]} is required"}
    assert not calls


def test_unknown_tool(calls):
    assert handler.lambda_handler({}, _context("drop_tables")) == {"error": "unknown tool drop_tables"}
    assert not calls


def test_query_retries_while_the_database_resumes(monkeypatch):
    resuming = ClientError({"Error": {"Code": "DatabaseResumingException", "Message": "resuming"}}, "ExecuteStatement")
    attempts = []

    class FakeRdsData:
        def execute_statement(self, **kwargs):
            attempts.append(kwargs)
            if len(attempts) < 3:
                raise resuming
            return {"formattedRecords": '[{"full_name": "Ben Carter"}]'}

    monkeypatch.setattr(handler, "_target", lambda region, account: ("cluster-arn", "secret-arn"))
    monkeypatch.setattr(handler.boto3, "client", lambda *a, **kw: FakeRdsData())
    monkeypatch.setattr(handler.time, "sleep", lambda s: None)

    assert handler._query("us-west-2", "1", "SELECT 1", "Ben") == [{"full_name": "Ben Carter"}]
    assert len(attempts) == 3
    assert attempts[0]["parameters"] == [{"name": "q", "value": {"stringValue": "%Ben%"}}]
    assert (attempts[0]["resourceArn"], attempts[0]["secretArn"]) == ("cluster-arn", "secret-arn")


def test_query_does_not_retry_other_errors(monkeypatch):
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "ExecuteStatement")

    class FakeRdsData:
        def execute_statement(self, **kwargs):
            raise denied

    monkeypatch.setattr(handler, "_target", lambda region, account: ("cluster-arn", "secret-arn"))
    monkeypatch.setattr(handler.boto3, "client", lambda *a, **kw: FakeRdsData())
    with pytest.raises(ClientError):
        handler._query("us-west-2", "1", "SELECT 1", "x")
