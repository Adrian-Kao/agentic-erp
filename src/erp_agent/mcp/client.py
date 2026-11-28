from typing import Any

from agents.mcp import MCPServerStreamableHttp
from agents.mcp.server import MCPServerStreamableHttpParams

from erp_agent.config import Settings


def connect_mcp(
    settings: Settings,
    key: str,
    request_id: str,
    *,
    httpx_client_factory: Any = None,
    timeout_seconds: float = 8,
) -> MCPServerStreamableHttp:
    params: MCPServerStreamableHttpParams = {
        "url": settings.mcp_server_url,
        "headers": {
            "Authorization": f"Bearer {settings.mcp_internal_token.get_secret_value()}",
            "X-API-Key": key,
            "X-Request-ID": request_id,
        },
        "timeout": timeout_seconds,
    }
    if httpx_client_factory is not None:
        params["httpx_client_factory"] = httpx_client_factory
    return MCPServerStreamableHttp(
        name="ERP-Accounting",
        params=params,
        client_session_timeout_seconds=timeout_seconds,
        max_retry_attempts=0,
        max_list_pages=2,
        use_structured_content=True,
    )
