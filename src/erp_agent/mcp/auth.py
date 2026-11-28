from contextvars import ContextVar
from hmac import compare_digest

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from erp_agent.application.services import Services
from erp_agent.contracts.errors import ServiceError
from erp_agent.observability.logging import request_id_var
from erp_agent.security.authorization import Principal

mcp_principal: ContextVar[Principal] = ContextVar("mcp_principal")


class MCPAuth:
    def __init__(self, app: ASGIApp, *, services: Services) -> None:
        self.app = app
        self.services = services

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        secret = self.services.settings.mcp_internal_token.get_secret_value()
        supplied = headers.get("authorization", "")
        try:
            if not secret or not compare_digest(supplied.encode(), f"Bearer {secret}".encode()):
                raise ServiceError(
                    "MCP_UNAUTHENTICATED", "Valid service authorization is required", 401
                )
            principal = self.services.auth.authenticate(headers.get("x-api-key"))
        except ServiceError as exc:
            response = JSONResponse(
                exc.response(request_id_var.get()).model_dump(), status_code=exc.status
            )
            await response(scope, receive, send)
            return
        token = mcp_principal.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            mcp_principal.reset(token)
