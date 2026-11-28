from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Lock
from time import sleep

import httpx
import pytest
from pydantic import ValidationError

from erp_agent.contracts.errors import ServiceError
from erp_agent.kyriba.client import KyribaClient
from erp_agent.kyriba.models import CashBalanceQuery, CashFlowQuery
from erp_agent.kyriba.service import KyribaReadService
from erp_agent.kyriba.settings import KyribaSettings
from erp_agent.security.authorization import Principal

TOKEN = "https://auth.test/oauth/token"
BASE = "https://api.test/api"
PRINCIPAL = Principal(tenant_id="tenant-a", actor_id="test", entity_ids=frozenset({"TW01"}))
MAPPING = {"tenant-a": {"TW01": {"company_code": "COMP01", "account_codes": ["ACC01"]}}}


def settings(**changes):
    return KyribaSettings(
        _env_file=None,
        **(
            {
                "mode": "live",
                "token_url": TOKEN,
                "base_url": BASE,
                "client_id": "demo-client",
                "client_secret": "demo-secret",
                "entity_map": MAPPING,
            }
            | changes
        ),
    )


def token(value="first", scheme="token", expires=300):
    return httpx.Response(
        200, json={"access_token": value, "token_type": scheme, "expires_in": expires}
    )


def empty_page():
    return httpx.Response(200, json={"results": [], "metadata": {"total": 50000}})


def gateway(fake, **options):
    return KyribaClient(settings(), transport=httpx.MockTransport(fake), **options)


def test_oauth_scheme_refresh_and_read_only_http_methods() -> None:
    expected = iter(
        [
            ("POST", "Basic ZGVtby1jbGllbnQ6ZGVtby1zZWNyZXQ=", token(scheme="tOkEn")),
            ("GET", "tOkEn first", httpx.Response(401)),
            ("GET", "Bearer first", httpx.Response(401)),
            ("POST", "Basic ZGVtby1jbGllbnQ6ZGVtby1zZWNyZXQ=", token("second", "Bearer")),
            ("GET", "Bearer second", empty_page()),
            ("GET", "Bearer second", empty_page()),
        ]
    )
    called = []

    def handler(req: httpx.Request) -> httpx.Response:
        method, auth, response = next(expected)
        assert (req.method, req.headers["Authorization"]) == (method, auth)
        if method == "POST":
            assert str(req.url) == TOKEN
            assert req.headers["content-type"] == "application/x-www-form-urlencoded"
            assert req.content == b"grant_type=client_credentials"
        else:
            assert req.url.host == "api.test"
            assert req.url.path == "/api/v1/companies"
            assert req.url.params["filter"] == "code==COMP01"
            assert req.url.params["page.limit"] == "10"
        called.append(method)
        return response

    client = gateway(handler)
    try:
        assert client.companies("COMP01", 0, 10) == []
        assert client.companies("COMP01", 0, 10) == []
        assert called == ["POST", "GET", "GET", "POST", "GET", "GET"]
    finally:
        client.close()


def test_token_is_reused_until_expiry_and_403_is_not_retried() -> None:
    calls = []
    clock = [0.0]

    def handler(req):
        calls.append(req.method)
        if req.method == "POST":
            return token(expires=10)
        return httpx.Response(403, json={"error": "vendor-private response"})

    client = gateway(handler, monotonic_clock=lambda: clock[0])
    try:
        for now in (0.0, 2.0, 10.0):
            clock[0] = now
            with pytest.raises(ServiceError) as error:
                client.companies("COMP01", 0, 10)
            assert error.value.code == "KYRIBA_FORBIDDEN"
            assert "vendor-private" not in str(error.value)
        assert calls == ["POST", "GET", "GET", "POST", "GET"]
    finally:
        client.close()


@pytest.mark.parametrize(
    ("reset", "waits", "expected_code"),
    [
        ("1002000", [2.0], None),
        ("1031000", [], "KYRIBA_RATE_LIMITED"),
        (None, [], "KYRIBA_RATE_LIMITED"),
    ],
)
def test_rate_limit_epoch_milliseconds_and_wait_cap(reset, waits, expected_code) -> None:
    now = [1000.0]
    observed = []
    get_calls = []

    def wait(seconds: float) -> None:
        observed.append(seconds)
        now[0] += seconds

    def handler(req):
        if req.method == "POST":
            return token()
        get_calls.append(req)
        if len(get_calls) == 1:
            headers = {"Kyriba-Customer-Rate-Limit-Reset": reset} if reset else {}
            return httpx.Response(429, headers=headers)
        return empty_page()

    client = gateway(
        handler, monotonic_clock=lambda: now[0], wall_clock=lambda: now[0], sleeper=wait
    )
    try:
        if expected_code:
            with pytest.raises(ServiceError) as error:
                client.companies("COMP01", 0, 10)
            assert error.value.code == expected_code and error.value.retryable
        else:
            assert client.companies("COMP01", 0, 10) == []
        assert observed == waits and len(get_calls) == (1 if expected_code else 2)
    finally:
        client.close()


@pytest.mark.parametrize(
    ("problem", "expected"),
    [
        ("timeout", "KYRIBA_TIMEOUT"),
        ("redirect", "KYRIBA_REQUEST_REJECTED"),
        ("malformed", "KYRIBA_INVALID_RESPONSE"),
        ("401", "KYRIBA_UNAUTHORIZED"),
    ],
)
def test_safe_failure_classification(problem, expected) -> None:
    paths = []

    def handler(req):
        paths.append(req.url.path)
        if req.method == "POST":
            return token()
        if problem == "timeout":
            raise httpx.ReadTimeout("private details", request=req)
        if problem == "redirect":
            return httpx.Response(307, headers={"location": "https://other.test/unsafe"})
        if problem == "401":
            return httpx.Response(401)
        return httpx.Response(200, content=b'{"not_results":false}')

    client = gateway(handler)
    try:
        with pytest.raises(ServiceError) as error:
            client.companies("COMP01", 0, 10)
        assert error.value.code == expected
        assert "/unsafe" not in paths and len(paths) == (5 if problem == "401" else 2)
    finally:
        client.close()


def test_cash_flow_exact_decimal_and_vendor_filter() -> None:
    def handler(req):
        if req.method == "POST":
            return token()
        assert req.url.path == "/api/v1/cash-flows"
        assert req.url.params["dateType"] == "VALUE"
        assert req.url.params["startDate"] == "2026-11-01"
        assert req.url.params["filter"] == "account.code==ACC01"
        assert req.url.params["sort"] == "flowId"
        return httpx.Response(
            200,
            content=b"""{"results":[{
            "uuid":"record-1","account":{"code":"ACC01"},"flowCode":{"code":"-WIRE"},
            "date":{"valueDate":"2026-11-20"},"flowAmount":{"currency":{"code":"USD"},
            "amount":0.123456789123456789,"signedAmount":-0.123456789123456789}
        }]}""",
        )

    config = settings()
    service = KyribaReadService(gateway(handler), config.entity_map, "live")
    try:
        query = CashFlowQuery(
            account_code="ACC01",
            date_type="VALUE",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
        )
        report = service.cash_flows(PRINCIPAL, "TW01", query).model_dump(mode="json")
        assert report["rows"][0]["signed_amount"] == "-0.123456789123456789"
        assert report["rows"][0]["amount"] == "0.123456789123456789"
    finally:
        service.close()


def test_balances_split_into_inclusive_31_day_windows() -> None:
    windows = []

    def handler(req):
        if req.method == "POST":
            return token()
        assert req.url.path == "/api/v1/cash-balances/accounts/ACC01/balances"
        params = req.url.params
        assert params["confirmedForecasts"] == "true" and params["dateType"] == "TRANSACTION"
        assert "page.limit" not in params
        windows.append((params["startDate"], params["endDate"]))
        return httpx.Response(
            200,
            json={
                "account": {"code": "ACC01"},
                "cashBalance": [
                    {
                        "amount": -50,
                        "balanceDate": {"date": params["endDate"], "dateType": "TRANSACTION"},
                        "currencyCode": "EUR",
                    }
                ],
            },
        )

    config = settings()
    service = KyribaReadService(gateway(handler), config.entity_map, "live")
    try:
        batch = service.cash_balances(
            PRINCIPAL,
            "TW01",
            CashBalanceQuery(
                account_code="ACC01",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 2, 2),
                confirmed_forecasts=True,
            ),
        )
        assert windows == [("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-02")]
        assert [str(item.amount) for item in batch.rows] == ["-50", "-50"]
        assert batch.next_offset is None
    finally:
        service.close()


def test_one_active_upstream_request_per_client() -> None:
    active, max_active = 0, 0
    counter_lock = Lock()

    def handler(req):
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        sleep(0.01)
        with counter_lock:
            active -= 1
        return token() if req.method == "POST" else empty_page()

    client = gateway(handler)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(lambda _: client.companies("COMP01", 0, 10), range(4))) == [
                [],
                [],
                [],
                [],
            ]
        assert max_active == 1
    finally:
        client.close()


def test_live_configuration_rejects_non_oauth_post_target_and_overlapping_entities() -> None:
    with pytest.raises(ValidationError, match="OAuth token endpoint"):
        settings(token_url="https://auth.test/v1/payments")
    with pytest.raises(ValidationError, match="exactly one"):
        settings(entity_map=MAPPING | {"tenant-b": MAPPING["tenant-a"]})
    with pytest.raises(ValidationError, match="HTTPS"):
        settings(base_url="http://api.test/api")


def test_pagination_uses_short_page_and_does_not_follow_vendor_links() -> None:
    offsets = []

    def handler(req):
        if req.method == "POST":
            return token()
        assert req.url.path == "/api/v1/third-parties"
        assert req.url.params["filter"] == "companyOwnership.code==COMP01"
        assert req.url.params["activeStatus"] == "OPENED"
        offset = int(req.url.params["page.offset"])
        offsets.append(offset)
        rows = [
            {
                "code": f"C{n}",
                "companyOwnership": {"code": "COMP01"},
                "name": "Counterparty",
                "activeStatus": "OPENED",
            }
            for n in range(offset, min(offset + 2, 3))
        ]
        return httpx.Response(
            200,
            json={
                "results": rows,
                "metadata": {"total": 0, "links": {"next": "https://untrusted.test/ignored"}},
            },
        )

    service = KyribaReadService(gateway(handler), settings().entity_map, "live", page_size=2)
    try:
        result = service.third_parties(PRINCIPAL, "TW01", "OPENED")
        assert offsets == [0, 2]
        assert [r.code for r in result.rows] == ["C0", "C1", "C2"]
        assert result.next_offset is None
    finally:
        service.close()


@pytest.mark.parametrize("resource", ["flows", "balances"])
def test_upstream_cannot_return_a_different_account(resource) -> None:
    def handler(req):
        if req.method == "POST":
            return token()
        if resource == "balances":
            return httpx.Response(200, json={"account": {"code": "DIFFERENT"}, "cashBalance": []})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "uuid": "flow-x",
                        "account": {"code": "DIFFERENT"},
                        "flowCode": {"code": "+WIRE"},
                        "date": {},
                        "flowAmount": {"amount": 1, "currency": {"code": "USD"}},
                    }
                ]
            },
        )

    service = KyribaReadService(gateway(handler), settings().entity_map, "live")
    dates = {"start_date": date(2026, 11, 1), "end_date": date(2026, 11, 30)}
    try:
        with pytest.raises(ServiceError) as error:
            if resource == "flows":
                service.cash_flows(PRINCIPAL, "TW01", CashFlowQuery(account_code="ACC01", **dates))
            else:
                service.cash_balances(
                    PRINCIPAL, "TW01", CashBalanceQuery(account_code="ACC01", **dates)
                )
        assert error.value.code == "KYRIBA_SCOPE_MISMATCH"
    finally:
        service.close()


@pytest.mark.parametrize(
    ("owner", "expected"),
    [
        (None, "KYRIBA_SCOPE_MISMATCH"),
        ({"code": "OTHER"}, "KYRIBA_SCOPE_MISMATCH"),
        ({"code": "COMP01"}, "KYRIBA_PAGINATION_LOOP"),
    ],
)
def test_missing_foreign_or_repeated_vendor_rows_fail_closed(owner, expected) -> None:
    def handler(req):
        if req.method == "POST":
            return token()
        return httpx.Response(200, json={"results": [{"code": "C1", "companyOwnership": owner}]})

    service = KyribaReadService(gateway(handler), settings().entity_map, "live", page_size=1)
    try:
        with pytest.raises(ServiceError) as error:
            service.third_parties(PRINCIPAL, "TW01")
        assert error.value.code == expected
    finally:
        service.close()
