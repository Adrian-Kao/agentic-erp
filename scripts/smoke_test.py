"""Verify the seeded deterministic API and optional network MCP / PostgreSQL roles."""

import argparse
import asyncio
import json
from time import monotonic, sleep
from uuid import uuid4

import httpx
from sqlalchemy import select, text

from erp_agent.config import Settings
from erp_agent.database.engine import database_engine
from erp_agent.database.tables import audit, runs
from erp_agent.mcp.client import connect_mcp

REQUEST = {
    "entity_ids": ["TW01"],
    "as_of_date": "2026-11-27",
    "horizon_months": 6,
    "currencies": ["USD"],
    "include_overdue_summary": True,
}


def find_key(settings: Settings, tenant: str) -> str:
    mapping = json.loads(settings.demo_api_keys_json.get_secret_value())
    for key, principal in mapping.items():
        if principal["tenant_id"] == tenant and "TW01" in principal["entity_ids"]:
            return str(key)
    raise SystemExit(f"Configure a TW01 demo principal for {tenant}")


def verify_api(base_url: str, key: str, other_key: str, *, request: dict | None = None) -> dict:
    body = REQUEST if request is None else request
    with httpx.Client(base_url=base_url, timeout=10) as client:
        deadline = monotonic() + 20
        while True:
            try:
                client.get("/readyz", timeout=2).raise_for_status()
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if monotonic() >= deadline:
                    raise SystemExit("API did not become ready within 20 seconds") from exc
                sleep(0.25)
        for path in ("/healthz", "/readyz", "/docs", "/openapi.json"):
            response = client.get(path)
            response.raise_for_status()
        path = "/v1/exposures/analyze"
        assert client.post(path, json=body).status_code == 401
        headers = {"X-API-Key": key, "Idempotency-Key": f"smoke-{uuid4()}"}
        response = client.post(path, json=body, headers=headers)
        response.raise_for_status()
        report = response.json()
        group = next(item for item in report["exposures"] if item["period"] == "2027-01")
        assert report["status"] == "COMPLETED" and report["validation_status"] == "PASSED"
        assert group["entity_id"] == "TW01" and group["currency"] == "USD"
        assert (group["ar_open"], group["ap_open"], group["net_exposure"], group["direction"]) == (
            "1200000.0000",
            "800000.0000",
            "400000.0000",
            "NET_INFLOW",
        )
        assert report["overdue_summary"][0]["overdue_ar"] == "25000.0000"
        detail_path = f"/v1/exposures/{report['run_id']}"
        assert client.get(detail_path, headers=headers).json() == report
        assert client.post(path, json=body, headers=headers).json() == report
        assert (
            client.post(path, json={**body, "horizon_months": 3}, headers=headers).status_code
            == 409
        )
        assert client.get(detail_path, headers={"X-API-Key": other_key}).status_code == 404
        print(
            json.dumps(
                {
                    "interface": "REST",
                    "request_id": response.headers["X-Request-ID"],
                    "run_id": report["run_id"],
                    "exposure": group,
                    "overdue": report["overdue_summary"],
                    "input_hash": report["provenance"]["input_hash"],
                    "isolation": "401 and 404",
                }
            )
        )
    return report


async def verify_mcp(
    settings: Settings,
    key: str,
    report: dict,
    kyriba_mock: bool = False,
    *,
    request: dict | None = None,
) -> None:
    async with connect_mcp(settings, key, str(uuid4())) as mcp:
        names = sorted(tool.name for tool in await mcp.list_tools())
        assert {
            "calc_net_exposure",
            "get_erp_schema",
            "get_exposure_report",
            "list_legal_entities",
            "query_erp_sql",
        } <= set(names)
        schema = await mcp.call_tool("get_erp_schema", {"topic": "exposure"})
        assert schema.structured_content and "exposure" in schema.structured_content["result"]
        output = await mcp.call_tool("calc_net_exposure", REQUEST if request is None else request)
        assert output.structured_content and output.structured_content["error"] is None
        actual = output.structured_content["result"]
        for field in report.keys() - {"run_id", "generated_at"}:
            assert actual[field] == report[field], field
        assert any(
            str(item.uri) == "erp://schema/exposure-v1"
            for item in (await mcp.list_resources()).resources
        )
        assert any(
            item.name == "analyze_fx_exposure" for item in (await mcp.list_prompts()).prompts
        )
        print(
            json.dumps(
                {
                    "interface": "MCP",
                    "run_id": actual["run_id"],
                    "tools": names,
                    "matches_rest": True,
                    "resource_and_prompt": "listed",
                }
            )
        )

        if kyriba_mock:
            query = {
                "account_code": "A_TW01_USD",
                "start_date": "2026-11-01",
                "end_date": "2026-11-30",
            }
            counts = {}
            for tool, arguments, count in (
                ("kyriba_list_companies", {"entity_id": "TW01"}, 1),
                ("kyriba_list_cash_flows", {"entity_id": "TW01", "query": query}, 2),
                ("kyriba_get_cash_balances", {"entity_id": "TW01", "query": query}, 1),
                ("kyriba_list_third_parties", {"entity_id": "TW01", "active_status": "OPENED"}, 2),
            ):
                response = await mcp.call_tool(tool, arguments)
                assert response.structured_content and response.structured_content["error"] is None
                batch = response.structured_content["result"]
                assert batch["mode"] == "mock" and batch["source_system"] == "KYRIBA"
                assert len(batch["rows"]) == count
                counts[tool] = count
            print(json.dumps({"interface": "Kyriba MCP", "mode": "mock", "row_counts": counts}))


def verify_pg_roles(settings: Settings, report: dict) -> None:
    reader = database_engine(settings.read_url, read=True)
    writer = database_engine(settings.database_url)
    assert reader.dialect.name == writer.dialect.name == "postgresql"
    sources = ("legal_entities", "ar_invoices", "ap_invoices", "payment_allocations")
    with reader.connect() as read, writer.connect() as write:
        for table in sources:
            assert read.scalar(
                text("SELECT has_table_privilege(current_user, :table, 'SELECT')"), {"table": table}
            )
            for right in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                for conn in (read, write):
                    assert not conn.scalar(
                        text("SELECT has_table_privilege(current_user, :table, :right)"),
                        {"table": table, "right": right},
                    )
            assert not write.scalar(
                text("SELECT has_table_privilege(current_user, :table, 'SELECT')"), {"table": table}
            )
        assert read.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        assert write.scalar(
            select(runs.c.run_id).where(
                runs.c.report_json["run_id"].as_string() == report["run_id"]
            )
        )
        assert write.scalar(select(audit.c.id).where(audit.c.operation == "analyze").limit(1))
    print("PostgreSQL role permissions, repeatable-read snapshot and persisted audit verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--kyriba-mock", action="store_true", help="Verify Kyriba mock MCP tools")
    parser.add_argument("--postgres-roles", action="store_true")
    options = parser.parse_args()
    settings = Settings()
    key, other = find_key(settings, "demo-a"), find_key(settings, "demo-b")
    report = verify_api(options.api, key, other)
    if options.mcp or options.kyriba_mock:
        asyncio.run(verify_mcp(settings, key, report, options.kyriba_mock))
    if options.postgres_roles:
        verify_pg_roles(settings, report)


if __name__ == "__main__":
    main()
