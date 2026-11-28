import json
import logging
from contextvars import ContextVar
from time import perf_counter
from uuid import UUID, uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from erp_agent.contracts.errors import ServiceError

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
logger = logging.getLogger("erp_agent")


def event(name: str, **fields: str | int | bool | None) -> None:
    logger.info(json.dumps({"event": name, "request_id": request_id_var.get(), **fields}))


class RequestBoundary:
    def __init__(
        self, app: ASGIApp, *, max_bytes: int = 32_768, accept_trace: bool = False
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.accept_trace = accept_trace

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        headers = dict(scope.get("headers", []))
        if self.accept_trace and b"x-request-id" in headers:
            try:
                request_id = str(UUID(headers[b"x-request-id"].decode("ascii")))
            except ValueError:
                request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        started = perf_counter()
        status = 500
        response_started = False

        async def send_traced(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            if scope["method"] in {"POST", "PUT", "PATCH"}:
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > self.max_bytes:
                        error = ServiceError("REQUEST_TOO_LARGE", "Request exceeds size limit", 413)
                        response = JSONResponse(
                            error.response(request_id).model_dump(), status_code=413
                        )
                        await response(scope, receive, send_traced)
                        return
                    if not message.get("more_body", False):
                        break

                replayed = False

                async def replay() -> Message:
                    nonlocal replayed
                    if replayed:
                        return await receive()
                    replayed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}

                await self.app(scope, replay, send_traced)
            else:
                await self.app(scope, receive, send_traced)
        except Exception as exc:
            event("internal_http_error", error_type=type(exc).__name__)
            if not response_started:
                error = ServiceError("INTERNAL_ERROR", "Internal server error", 500)
                response = JSONResponse(error.response(request_id).model_dump(), status_code=500)
                await response(scope, receive, send_traced)
        finally:
            event(
                "http_request",
                method=scope["method"],
                status=status,
                duration_ms=int((perf_counter() - started) * 1000),
            )
            request_id_var.reset(token)
