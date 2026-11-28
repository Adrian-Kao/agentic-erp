import json
from time import perf_counter
from typing import Any
from uuid import UUID

from agents import function_tool
from agents.mcp import MCPServer
from agents.tool import Tool
from pydantic import BaseModel, Field, ValidationError

from erp_agent.contracts.agent import ToolTrace
from erp_agent.contracts.exposure import ExposureReport
from erp_agent.contracts.requests import ExposureRequest, QueryFilters, ScenarioName
from erp_agent.database.named_queries import QUERY_NAMES
from erp_agent.observability.logging import event
from erp_agent.security.authorization import Principal

ALLOWED_TOOLS = frozenset(
    {
        "get_erp_schema",
        "list_legal_entities",
        "query_erp_sql",
        "calc_net_exposure",
        "get_exposure_report",
    }
)
MAX_CALLS = 6


class WorkingState(BaseModel):
    task_id: str
    calls: int = 0
    trace: list[ToolTrace] = Field(default_factory=list)
    reports: dict[str, list[str]] = Field(default_factory=dict)
    information: str | None = None


def build_tools(
    mcp: MCPServer, principal: Principal, state: WorkingState, as_of_date: str | None
) -> list[Tool]:
    async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if state.calls >= MAX_CALLS:
            return {"error": "TOOL_CALL_LIMIT"}
        state.calls += 1
        started = perf_counter()
        outcome = "OK"
        try:
            if name not in ALLOWED_TOOLS:
                outcome = "TOOL_NOT_ALLOWED"
                return {"error": outcome}
            response = await mcp.call_tool(name, args)
            payload = response.structured_content
            if response.is_error or not isinstance(payload, dict):
                outcome = "INVALID_TOOL_RESPONSE"
                return {"error": outcome}
            error = payload.get("error")
            if error is not None:
                outcome = str(error.get("code", "TOOL_ERROR"))[:64]
                return {"error": outcome}
            result = payload["result"]
            if name in {"calc_net_exposure", "get_exposure_report"}:
                report = ExposureReport.model_validate(result)
                run_id = str(report.run_id)
                scope = args.get("entity_ids") or sorted(
                    {row.entity_id for row in report.exposures}
                    | {row.entity_id for row in report.overdue_summary}
                )
                state.reports[run_id] = scope
                return {
                    "run_id": run_id,
                    "status": report.status,
                    "validation_status": report.validation_status,
                    "as_of_date": report.as_of_date.isoformat(),
                    "scenario": report.provenance.scenario,
                    "source_system": report.provenance.source_system,
                    "source_rows_read": report.provenance.source_rows_read,
                    "overdue_groups": len(report.overdue_summary),
                    "warning_codes": sorted({issue.code for issue in report.data_quality_issues})[
                        :20
                    ],
                }
            if name == "list_legal_entities":
                codes = sorted(
                    str(row["entity_id"])
                    for row in result
                    if row["entity_id"] in principal.entity_ids
                )
                state.information = "已授權的有效法人為 " + ("、".join(codes) or "無")
                return {"entity_ids": codes}
            if name == "query_erp_sql":
                state.information = "已查詢授權帳款。可用曝險工具產生含金額與來源的報表。"
                return {
                    "rows_returned": len(result["rows"]),
                    "truncated": result["truncated"],
                    "warning_codes": sorted({issue["code"] for issue in result["issues"]})[:20],
                }
            if len(json.dumps(result).encode()) > 8_192:
                outcome = "TOOL_OUTPUT_LIMIT"
                return {"error": outcome}
            state.information = (
                "ERP 提供法人、本位幣、計價幣別、到期日、文件狀態與未結餘額。"
                "完整欄位可透過 /v1/erp/schema 查詢。"
            )
            return {"schema": result}
        except (KeyError, TypeError, ValidationError):
            outcome = "INVALID_TOOL_RESPONSE"
            return {"error": outcome}
        except Exception as exc:
            outcome = "MCP_UNAVAILABLE"
            event("agent_mcp_error", error_type=type(exc).__name__)
            return {"error": outcome}
        finally:
            duration = int((perf_counter() - started) * 1000)
            state.trace.append(ToolTrace(tool=name, outcome=outcome, duration_ms=duration))
            event("agent_tool", tool=name, result=outcome, duration_ms=duration)

    @function_tool
    async def get_erp_schema(topic: str | None = None) -> str:
        """Read the approved ERP schema and available accounting queries."""
        return json.dumps(await call("get_erp_schema", {"topic": topic}))

    @function_tool
    async def list_legal_entities() -> str:
        """List active legal-entity codes available to this user."""
        return json.dumps(await call("list_legal_entities", {}))

    @function_tool
    async def query_erp_sql(query_name: str, filters: QueryFilters, limit: int = 25) -> str:
        """Inspect counts for open_ar, open_ap, overdue_ar, overdue_ap, entity_list, or source_lineage."""
        if query_name not in QUERY_NAMES:
            if state.calls < MAX_CALLS:
                state.calls += 1
                state.trace.append(
                    ToolTrace(tool="query_erp_sql", outcome="QUERY_NOT_ALLOWED", duration_ms=0)
                )
                event("agent_tool", tool="query_erp_sql", result="QUERY_NOT_ALLOWED", duration_ms=0)
            return json.dumps({"error": "QUERY_NOT_ALLOWED"})
        return json.dumps(
            await call(
                "query_erp_sql",
                {
                    "query_name": query_name,
                    "filters": filters.model_dump(mode="json", exclude_none=True),
                    "limit": limit,
                },
            )
        )

    @function_tool
    async def calc_net_exposure(
        entity_ids: list[str],
        horizon_months: int = 6,
        snapshot_date: str | None = None,
        currencies: list[str] | None = None,
        include_overdue_summary: bool = True,
        scenario: ScenarioName | None = None,
    ) -> str:
        """Compute exposure as of the ISO date. Choose the scenario only when requested."""
        # Validate at the MCP boundary too. Do not pass caller-controlled auth fields.
        args = {
            "entity_ids": entity_ids,
            "horizon_months": horizon_months,
            "as_of_date": snapshot_date or as_of_date,
            "currencies": currencies,
            "include_overdue_summary": include_overdue_summary,
            "scenario": scenario,
        }
        try:
            params = ExposureRequest.model_validate(args)
        except ValidationError:
            return json.dumps({"error": "INVALID_ANALYSIS_PARAMETERS"})
        return json.dumps(await call("calc_net_exposure", params.model_dump(mode="json")))

    @function_tool
    async def get_exposure_report(run_id: UUID) -> str:
        """Retrieve validation metadata for an already persisted ERP run."""
        return json.dumps(await call("get_exposure_report", {"run_id": str(run_id)}))

    return [
        get_erp_schema,
        list_legal_entities,
        query_erp_sql,
        calc_net_exposure,
        get_exposure_report,
    ]
