import json
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from threading import Lock
from time import monotonic, sleep, time
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx
from pydantic import Field, TypeAdapter, ValidationError

from erp_agent.contracts.errors import ServiceError
from erp_agent.kyriba.models import (
    ActiveStatus,
    CashBalanceQuery,
    CashFlowQuery,
    ProviderCode,
    WireModel,
)
from erp_agent.kyriba.settings import KyribaSettings

_MAX_RESPONSE_BYTES = 2_097_152
_RESET_HEADER = "Kyriba-Customer-Rate-Limit-Reset"
_LIST_PATHS = {
    "companies": "/v1/companies",
    "cash_flows": "/v1/cash-flows",
    "third_parties": "/v1/third-parties",
}
ListRoute = Literal["companies", "cash_flows", "third_parties"]
WireRow = dict[str, Any]
_PROVIDER_CODE = TypeAdapter(ProviderCode)


class KyribaReadGateway(Protocol):
    def companies(self, company: str, offset: int, limit: int) -> list[WireRow]: ...

    def cash_flows(self, query: CashFlowQuery, offset: int, limit: int) -> list[WireRow]: ...

    def cash_balances(self, query: CashBalanceQuery, start: date, end: date) -> WireRow: ...

    def third_parties(
        self, company: str, active_status: ActiveStatus | None, offset: int, limit: int
    ) -> list[WireRow]: ...

    def close(self) -> None: ...


class _TokenResponse(WireModel):
    access_token: str = Field(pattern=r"^[!-~]+$", max_length=16_384)
    token_type: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    expires_in: int = Field(default=60, gt=0)


class _Page(WireModel):
    results: list[WireRow]


@dataclass(frozen=True)
class _Token:
    value: str
    scheme: str
    expires_at: float


@dataclass(frozen=True)
class _Response:
    status: int
    reset: str | None
    body: bytes

    def object(self) -> WireRow:
        try:
            content = json.loads(self.body, parse_float=Decimal)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise _invalid_response() from exc
        if not isinstance(content, dict):
            raise _invalid_response()
        return content


def _invalid_response() -> ServiceError:
    return ServiceError("KYRIBA_INVALID_RESPONSE", "Kyriba returned an unexpected response", 502)


def _rate_limited() -> ServiceError:
    return ServiceError(
        "KYRIBA_RATE_LIMITED", "Kyriba request is rate limited", 429, retryable=True
    )


class KyribaClient:
    """One simultaneous upstream request per client, including OAuth exchanges."""

    def __init__(
        self,
        settings: KyribaSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], float] = time,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if settings.mode != "live" or not (
            settings.token_url
            and settings.base_url
            and settings.client_id
            and settings.client_secret
        ):
            raise ValueError("KyribaClient requires validated live settings")
        self._base_url = str(settings.base_url).rstrip("/")
        self._token_url = str(settings.token_url)
        self._basic = httpx.BasicAuth(
            settings.client_id.get_secret_value(), settings.client_secret.get_secret_value()
        )
        self._http = httpx.Client(
            timeout=settings.timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )
        self._lock = Lock()
        self._deadline = settings.timeout_seconds
        self._max_wait = settings.retry_wait_seconds
        self._monotonic = monotonic_clock
        self._wall_clock = wall_clock
        self._sleep = sleeper
        self._token: _Token | None = None
        self._request_times: deque[float] = deque()
        self._retry_after = 0.0

    def close(self) -> None:
        self._http.close()

    def companies(self, company: str, offset: int, limit: int) -> list[WireRow]:
        company = _PROVIDER_CODE.validate_python(company)
        return self._page(
            "companies", {"filter": f"code=={company}", "sort": "code"}, offset, limit
        )

    def cash_flows(self, query: CashFlowQuery, offset: int, limit: int) -> list[WireRow]:
        return self._page(
            "cash_flows",
            {
                **query.parameters(),
                "dateType": query.date_type,
                "filter": f"account.code=={query.account_code}",
                "sort": "flowId",
            },
            offset,
            limit,
        )

    def cash_balances(self, query: CashBalanceQuery, start: date, end: date) -> WireRow:
        account = quote(query.account_code, safe="")
        return self._get(
            f"/v1/cash-balances/accounts/{account}/balances",
            {**query.parameters(), "startDate": start.isoformat(), "endDate": end.isoformat()},
        )

    def third_parties(
        self, company: str, active_status: ActiveStatus | None, offset: int, limit: int
    ) -> list[WireRow]:
        company = _PROVIDER_CODE.validate_python(company)
        params = {"filter": f"companyOwnership.code=={company}", "sort": "code"}
        if active_status is not None:
            params["activeStatus"] = active_status
        return self._page("third_parties", params, offset, limit)

    def _page(
        self, route: ListRoute, params: dict[str, str], offset: int, limit: int
    ) -> list[WireRow]:
        body = self._get(
            _LIST_PATHS[route], {**params, "page.offset": str(offset), "page.limit": str(limit)}
        )
        try:
            page = _Page.model_validate(body)
        except ValidationError as exc:
            raise _invalid_response() from exc
        if len(page.results) > limit:
            raise _invalid_response()
        return page.results

    def _get(self, path: str, params: Mapping[str, str]) -> WireRow:
        if not self._lock.acquire(timeout=self._deadline):
            raise ServiceError("KYRIBA_BUSY", "Kyriba client is busy", 503, retryable=True)
        try:
            token = self._get_token()
            reply = self._authorized_get(path, params, token, token.scheme)
            if reply.status == 401:
                other = {"bearer": "token", "token": "Bearer"}.get(token.scheme.lower())
                if other:
                    reply = self._authorized_get(path, params, token, other)
                if reply.status == 401:
                    self._token = None
                    token = self._get_token()
                    reply = self._authorized_get(path, params, token, token.scheme)
            if reply.status == 401:
                self._token = None
            self._check(reply.status)
            return reply.object()
        finally:
            self._lock.release()

    def _get_token(self) -> _Token:
        now = self._monotonic()
        if self._token is not None and self._token.expires_at > now:
            return self._token
        reply = self._request(
            "POST", self._token_url, auth=self._basic, data={"grant_type": "client_credentials"}
        )
        self._check(reply.status)
        try:
            token = _TokenResponse.model_validate(reply.object())
        except ValidationError as exc:
            raise _invalid_response() from exc
        refresh_at = self._monotonic() + token.expires_in - min(30, token.expires_in / 10)
        self._token = _Token(token.access_token, token.token_type, refresh_at)
        return self._token

    def _authorized_get(
        self, path: str, params: Mapping[str, str], token: _Token, scheme: str
    ) -> _Response:
        return self._request(
            "GET",
            self._base_url + path,
            params=params,
            headers={"Authorization": f"{scheme} {token.value}"},
        )

    def _request(self, method: Literal["POST", "GET"], url: str, **kwargs: Any) -> _Response:
        for attempt in range(2):
            reply = self._send(method, url, **kwargs)
            if reply.status != 429:
                return reply
            reset = reply.reset
            if reset is None or not reset.isascii() or not reset.isdigit() or len(reset) > 15:
                self._retry_after = max(self._retry_after, self._wall_clock() + 30)
                break
            self._retry_after = max(self._retry_after, int(reset) / 1000)
            if attempt == 0:
                continue
        raise _rate_limited()

    def _send(self, method: Literal["POST", "GET"], url: str, **kwargs: Any) -> _Response:
        self._wait(self._retry_after - self._wall_clock())
        now = self._monotonic()
        while self._request_times and now - self._request_times[0] >= 30:
            self._request_times.popleft()
        # Stay below both the standard and the reduced overuse quota.
        if len(self._request_times) >= 25:
            self._wait(30 - (now - self._request_times[0]))
            now = self._monotonic()
            while self._request_times and now - self._request_times[0] >= 30:
                self._request_times.popleft()
            if len(self._request_times) >= 25:
                raise _rate_limited()
        self._request_times.append(now)
        try:
            with self._http.stream(method, url, follow_redirects=False, **kwargs) as response:
                body = bytearray()
                if response.status_code == 200:
                    for chunk in response.iter_bytes(chunk_size=65_536):
                        body.extend(chunk)
                        if len(body) > _MAX_RESPONSE_BYTES:
                            raise ServiceError(
                                "KYRIBA_RESPONSE_LIMIT",
                                "Kyriba response exceeds the size limit",
                                502,
                            )
                return _Response(
                    response.status_code, response.headers.get(_RESET_HEADER), bytes(body)
                )
        except httpx.TimeoutException as exc:
            raise ServiceError(
                "KYRIBA_TIMEOUT", "Kyriba request timed out", 504, retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ServiceError(
                "KYRIBA_UNAVAILABLE", "Kyriba connection failed", 503, retryable=True
            ) from exc

    def _wait(self, delay: float) -> None:
        if delay > self._max_wait:
            raise _rate_limited()
        if delay > 0:
            self._sleep(delay)

    @staticmethod
    def _check(status: int) -> None:
        if status == 200:
            return
        if status == 401:
            raise ServiceError("KYRIBA_UNAUTHORIZED", "Kyriba authentication failed", 401)
        if status == 403:
            raise ServiceError("KYRIBA_FORBIDDEN", "Kyriba permission denied", 403)
        if status == 404:
            raise ServiceError("KYRIBA_NOT_FOUND", "Kyriba resource not found", 404)
        if status >= 500:
            raise ServiceError(
                "KYRIBA_UNAVAILABLE", "Kyriba service unavailable", 503, retryable=True
            )
        raise ServiceError("KYRIBA_REQUEST_REJECTED", "Kyriba rejected the configured request", 502)
