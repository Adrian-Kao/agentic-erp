from uuid import UUID

import httpx2
from sqlalchemy import select

from erp_agent.config import Settings
from erp_agent.database.engine import database_engine
from erp_agent.database.tables import audit
from erp_agent.kyriba.settings import KyribaSettings
from erp_agent.mcp.client import connect_mcp
from erp_agent.mcp.server import create_mcp_app


async def test_kyriba_mock_mcp_tools_and_tenant_scope(demo_settings: Settings) -> None:
    source = KyribaSettings(_env_file=None, mode="mock", page_size=1, max_pages=2)
    app = create_mcp_app(demo_settings, kyriba_settings=source)
    trace = str(UUID(int=504))
    parameters = {
        "entity_id": "TW01",
        "query": {
            "account_code": "A_TW01_USD",
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
        },
    }

    async with app.router.lifespan_context(app):

        def factory(**kwargs):
            return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)

        async with connect_mcp(demo_settings, "test-a", trace, httpx_client_factory=factory) as mcp:
            names = {tool.name for tool in await mcp.list_tools()}
            assert {name for name in names if name.startswith("kyriba_")} == {
                "kyriba_list_companies",
                "kyriba_list_cash_flows",
                "kyriba_get_cash_balances",
                "kyriba_list_third_parties",
            }
            company = await mcp.call_tool("kyriba_list_companies", {"entity_id": "TW01"})
            assert company.structured_content["result"]["rows"][0]["code"] == "DEMO_A_TW01"
            flows = (await mcp.call_tool("kyriba_list_cash_flows", parameters)).structured_content[
                "result"
            ]
            assert flows["mode"] == "mock" and flows["source_system"] == "KYRIBA"
            assert len(flows["rows"]) == 2 and flows["next_offset"] == 2
            assert flows["rows"][0]["signed_amount"] == "10580.123456"
            assert flows["rows"][1]["signed_amount"] == "-1500.25"
            balances = await mcp.call_tool("kyriba_get_cash_balances", parameters)
            result = balances.structured_content["result"]
            assert result["rows"][0]["amount"] == "20000.00"
            assert result["next_offset"] is None
            parties = await mcp.call_tool(
                "kyriba_list_third_parties", {"entity_id": "TW01", "active_status": "OPENED"}
            )
            result = parties.structured_content["result"]
            assert len(result["rows"]) == 2 and result["next_offset"] == 2
            assert all(row["company_code"] == "DEMO_A_TW01" for row in result["rows"])
            more = await mcp.call_tool(
                "kyriba_list_third_parties", {"entity_id": "TW01", "offset": 2}
            )
            result = more.structured_content["result"]
            assert len(result["rows"]) == 1 and result["next_offset"] is None
            forbidden = await mcp.call_tool("kyriba_list_companies", {"entity_id": "TW02"})
            assert forbidden.structured_content["error"]["code"] == "ENTITY_FORBIDDEN"
        async with connect_mcp(demo_settings, "test-b", trace, httpx_client_factory=factory) as mcp:
            other = await mcp.call_tool("kyriba_list_companies", {"entity_id": "TW01"})
            assert other.structured_content["result"]["rows"][0]["code"] == "DEMO_B_TW01"
            forbidden = await mcp.call_tool("kyriba_list_cash_flows", parameters)
            assert forbidden.structured_content["error"]["code"] == "KYRIBA_SCOPE_FORBIDDEN"
            assert forbidden.structured_content["result"] is None
        async with factory(base_url=demo_settings.mcp_server_url) as raw:
            response = await raw.post("/mcp", json={}, headers={"X-API-Key": "test-a"})
            assert response.status_code == 401

    with database_engine(demo_settings.database_url).connect() as conn:
        row = conn.execute(select(audit).where(audit.c.operation == "kyriba_list_cash_flows")).one()
        assert row.tenant_id == "demo-a"
        assert row.metadata_json == {"request_id": trace, "source_rows": 2, "issue_count": None}
