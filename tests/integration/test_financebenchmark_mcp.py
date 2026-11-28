from uuid import uuid4

import httpx2
from fastapi.testclient import TestClient

from erp_agent.api.main import create_app
from erp_agent.config import Settings
from erp_agent.kyriba.settings import KyribaSettings
from erp_agent.mcp.client import connect_mcp
from erp_agent.mcp.server import create_mcp_app


async def test_financebenchmark_mcp_parity_and_allowed_lineage(finance_settings: Settings) -> None:
    body = {
        "entity_ids": ["TW01"],
        "as_of_date": "2026-11-27",
        "currencies": ["USD"],
        "scenario": "baseline",
    }
    server = create_mcp_app(
        finance_settings, kyriba_settings=KyribaSettings(_env_file=None, mode="disabled")
    )
    with TestClient(create_app(finance_settings)) as api:
        rest = api.post("/v1/exposures/analyze", json=body, headers={"X-API-Key": "analyst"}).json()
    async with server.router.lifespan_context(server):

        def factory(**kwargs):
            return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=server), **kwargs)

        async with connect_mcp(
            finance_settings, "analyst", str(uuid4()), httpx_client_factory=factory
        ) as client:
            assert {tool.name for tool in await client.list_tools()} == {
                "get_erp_schema",
                "list_legal_entities",
                "query_erp_sql",
                "calc_net_exposure",
                "get_exposure_report",
            }
            result = await client.call_tool("calc_net_exposure", body)
            mcp = result.structured_content["result"]
            assert {k: v for k, v in mcp.items() if k not in {"run_id", "generated_at"}} == {
                k: v for k, v in rest.items() if k not in {"run_id", "generated_at"}
            }
            assert mcp["provenance"]["source_rows_read"] == 8887
            schema = await client.call_tool("get_erp_schema", {"topic": "dataset"})
            assert (
                schema.structured_content["result"]["dataset"]["default_mysql_scenario"]
                == "baseline"
            )
            query = await client.call_tool(
                "query_erp_sql", {"query_name": "source_lineage", "filters": body, "limit": 3}
            )
            rows = query.structured_content["result"]["rows"]
            assert len(rows) == 3 and "raw_json" not in rows[0]
            denied = await client.call_tool(
                "query_erp_sql",
                {
                    "query_name": "source_lineage",
                    "filters": {**body, "entity_ids": ["TW02"]},
                },
            )
            assert denied.structured_content["error"]["code"] == "ENTITY_FORBIDDEN"
            rejected = await client.call_tool(
                "query_erp_sql", {"query_name": "raw_customers", "filters": body}
            )
            assert rejected.structured_content["error"]["code"] == "QUERY_NOT_ALLOWED"
            resource = await client.read_resource("erp://schema/exposure-v1")
            assert "scenario_rows_derived" in resource.contents[0].text
