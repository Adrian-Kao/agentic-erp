from uuid import UUID

import httpx2
from fastapi.testclient import TestClient

from erp_agent.config import Settings
from erp_agent.mcp.client import connect_mcp

BODY = {
    "entity_ids": ["TW01"],
    "as_of_date": "2026-11-27",
    "horizon_months": 6,
    "currencies": ["USD"],
}


async def test_streamable_mcp_parity_and_guardrails(
    demo_settings: Settings,
    mcp_factory,
    client: TestClient,
) -> None:
    trace = str(UUID(int=42))
    async with connect_mcp(demo_settings, "test-a", trace, httpx_client_factory=mcp_factory) as mcp:
        names = {tool.name for tool in await mcp.list_tools()}
        assert names == {
            "get_erp_schema",
            "list_legal_entities",
            "query_erp_sql",
            "calc_net_exposure",
            "get_exposure_report",
        }
        schema = await mcp.call_tool("get_erp_schema", {"topic": "queries"})
        assert "open_ar" in schema.structured_content["result"]["queries"]
        report = (await mcp.call_tool("calc_net_exposure", BODY)).structured_content["result"]
        assert report["exposures"][0]["net_exposure"] == "400000.0000"
        rest = client.post(
            "/v1/exposures/analyze", json=BODY, headers={"X-API-Key": "test-a"}
        ).json()
        assert {k: v for k, v in report.items() if k not in {"run_id", "generated_at"}} == {
            k: v for k, v in rest.items() if k not in {"run_id", "generated_at"}
        }
        read = await mcp.call_tool("get_exposure_report", {"run_id": report["run_id"]})
        assert read.structured_content["result"] == report
        query = await mcp.call_tool(
            "query_erp_sql",
            {
                "query_name": "open_ar",
                "filters": {"as_of_date": "2026-11-27", "currencies": ["USD"]},
                "limit": 1,
            },
        )
        assert query.structured_content["result"]["truncated"]
        for raw_sql in ("DROP TABLE ar_invoices", "SELECT * FROM ar_invoices"):
            rejected = await mcp.call_tool("query_erp_sql", {"query_name": raw_sql, "filters": {}})
            assert rejected.structured_content["error"]["code"] == "QUERY_NOT_ALLOWED"
            assert rejected.structured_content["error"]["request_id"] == trace
        forbidden = await mcp.call_tool("calc_net_exposure", {**BODY, "entity_ids": ["TW02"]})
        assert forbidden.structured_content["error"]["code"] == "ENTITY_FORBIDDEN"
    async with connect_mcp(
        demo_settings, "test-b", trace, httpx_client_factory=mcp_factory
    ) as other:
        read = await other.call_tool("get_exposure_report", {"run_id": report["run_id"]})
        assert read.structured_content["error"]["code"] == "REPORT_NOT_FOUND"


async def test_mcp_resource_prompt_and_auth(demo_settings: Settings, mcp_factory, mcp_http) -> None:
    async with connect_mcp(
        demo_settings,
        "test-a",
        str(UUID(int=42)),
        httpx_client_factory=mcp_factory,
    ) as client:
        assert {str(r.uri) for r in (await client.list_resources()).resources} == {
            "erp://schema/exposure-v1"
        }
        resource = await client.read_resource("erp://schema/exposure-v1")
        assert "existing_hedges_included" in resource.contents[0].text
        assert "analyze_fx_exposure" in {p.name for p in (await client.list_prompts()).prompts}
        assert (await client.get_prompt("analyze_fx_exposure")).messages
    async with httpx2.AsyncClient(
        transport=mcp_http, base_url=demo_settings.mcp_server_url
    ) as http:
        assert (await http.post("/mcp", json={})).status_code == 401
        assert (
            await http.post("/mcp", json={}, headers={"X-API-Key": "test-a"})
        ).status_code == 401
