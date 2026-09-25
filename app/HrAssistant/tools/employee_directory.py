"""Employee directory tools from AgentCore Gateway (MCP) -> Lambda EmployeeDirectory -> Aurora (Data API).

The CDK app injects AGENTCORE_GATEWAY_HRGATEWAY_URL and grants bedrock-agentcore:InvokeGateway to the
runtime role. The gateway uses AWS_IAM auth, so every MCP HTTP request is SigV4-signed with the
runtime's credentials. Tool names arrive as `EmployeeDirectory___<tool>`.
"""

import os

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from strands.tools.mcp import MCPClient

GATEWAY_URL = os.getenv("AGENTCORE_GATEWAY_HRGATEWAY_URL", "")
REGION = os.getenv("AWS_REGION")
TOOL_PREFIX = "EmployeeDirectory___"


class _SigV4(httpx.Auth):
    requires_request_body = True

    def __init__(self, region: str):
        self._region = region
        self._session = boto3.Session()

    def auth_flow(self, request: httpx.Request):
        signed = AWSRequest(method=request.method, url=str(request.url), data=request.content,
                            headers={k: v for k, v in request.headers.items() if k.lower() != "connection"})
        SigV4Auth(self._session.get_credentials().get_frozen_credentials(),
                  "bedrock-agentcore", self._region).add_auth(signed)
        request.headers.update(dict(signed.headers))
        yield request


def employee_directory_client() -> MCPClient | None:
    """Strands tool provider for the gateway; None when running without a deployed gateway."""
    if not GATEWAY_URL:
        return None
    region = REGION or boto3.Session().region_name
    # continue_on_error: the agent still answers handbook questions if the gateway is unreachable
    return MCPClient(url=GATEWAY_URL, auth_provider=_SigV4(region), continue_on_error=True,
                     application_name="HrAssistant")
