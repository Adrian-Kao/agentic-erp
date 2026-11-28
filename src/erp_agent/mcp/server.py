import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date
from time import perf_counter
from typing import Annotated, Any, Generic, TypeVar
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.routing import Mount

from erp_agent.application.query_service import EntityRow, QueryResult
from erp_agent.application.services import Services
from erp_agent.config import Settings
from erp_agent.contracts.errors import ErrorResponse, ServiceError
from erp_agent.contracts.exposure import ExposureReport
from erp_agent.contracts.requests import (
    Currency,
    EntityCode,
    ExposureRequest,
    QueryFilters,
    ScenarioName,
)
from erp_agent.database.repository import QueryLimitExceeded
from erp_agent.kyriba.models import (
    ActiveStatus,
    CashBalanceQuery,
    CashFlowQuery,
    KyribaBatch,
    KyribaCashBalance,
    KyribaCashFlow,
    KyribaCompany,
    KyribaThirdParty,
    Offset,
)
from erp_agent.kyriba.service import create_kyriba_service
from erp_agent.kyriba.settings import KyribaSettings
from erp_agent.mcp.auth import MCPAuth, mcp_principal
from erp_agent.observability.audit import write_audit
from erp_agent.observability.logging import RequestBoundary, event, request_id_var

T = TypeVar("T")


class ToolReply(BaseModel, Generic[T]):
    result: T | None = None
    error: ErrorResponse | None = None


def create_mcp_app(
    settings: Settings | None = None, *, kyriba_settings: KyribaSettings | None = None
) -> Starlette:
    config = settings or Settings()
    logging.getLogger("erp_agent").setLevel(config.log_level.upper())
    services = Services(config)
    kyriba = create_kyriba_service(kyriba_settings or KyribaSettings())
    server = MCPServer("ERP-Accounting", version="1.0.0", subscriptions=False)

    async def call(
        name: str, reply_type: type[ToolReply[T]], action: Callable[[], T]
    ) -> ToolReply[T]:
        started = perf_counter()
        rid = request_id_var.get()

        def run() -> ToolReply[T]:
            failure: ServiceError
            try:
                value = action()
                if name in {"get_erp_schema", "list_legal_entities", "query_erp_sql"} or isinstance(
                    value, KyribaBatch
                ):
                    with services.writer.begin() as conn:
                        write_audit(
                            conn,
                            mcp_principal.get(),
                            name,
                            "OK",
                            rid,
                            source_rows=len(value.rows)
                            if isinstance(value, (QueryResult, KyribaBatch))
                            else None,
                        )
                reply = reply_type(result=value)
                if len(reply.model_dump_json().encode()) > 2_097_152:
                    raise ServiceError(
                        "OUTPUT_LIMIT", "Result exceeds the MCP response size limit", 422
                    )
                return reply
            except ServiceError as exc:
                failure = exc
            except ValidationError:
                failure = ServiceError("INVALID_REQUEST", "Tool parameters are invalid", 422)
            except QueryLimitExceeded:
                failure = ServiceError("QUERY_LIMIT", "Snapshot row limit exceeded", 422)
            except SQLAlchemyError:
                failure = ServiceError(
                    "DATABASE_UNAVAILABLE", "Database unavailable", 503, retryable=True
                )
            except Exception as exc:
                event("mcp_internal_error", error_type=type(exc).__name__)
                failure = ServiceError("INTERNAL_ERROR", "Internal server error", 500)
            return reply_type(error=failure.response(rid))

        answer = await run_in_threadpool(run)
        report = answer.result if isinstance(answer.result, ExposureReport) else None
        event(
            "mcp_tool",
            tool=name,
            ok=answer.error is None,
            run_id=str(report.run_id) if report else None,
            error_code=answer.error.code if answer.error else None,
            issue_count=len(report.data_quality_issues) if report else None,
            duration_ms=int((perf_counter() - started) * 1000),
        )
        return answer

    @server.tool()
    async def get_erp_schema(topic: str | None = None) -> ToolReply[dict[str, Any]]:
        """Read approved business schema and query names."""
        return await call(
            "get_erp_schema", ToolReply[dict[str, Any]], lambda: services.queries.schema(topic)
        )

    @server.tool()
    async def list_legal_entities(
        entity_ids: Annotated[list[EntityCode] | None, Field(min_length=1, max_length=20)] = None,
    ) -> ToolReply[list[EntityRow]]:
        """List active legal entities in the authenticated scope."""
        return await call(
            "list_legal_entities",
            ToolReply[list[EntityRow]],
            lambda: services.queries.entities(mcp_principal.get(), entity_ids),
        )

    @server.tool()
    async def query_erp_sql(
        query_name: str,
        filters: QueryFilters,
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> ToolReply[QueryResult]:
        """Run one approved named ERP query. Raw SQL is rejected."""
        return await call(
            "query_erp_sql",
            ToolReply[QueryResult],
            lambda: services.queries.named_query(query_name, filters, limit, mcp_principal.get()),
        )

    @server.tool()
    async def calc_net_exposure(
        entity_ids: Annotated[list[EntityCode], Field(min_length=1, max_length=20)],
        as_of_date: date,
        horizon_months: Annotated[int, Field(ge=3, le=6)] = 6,
        currencies: Annotated[list[Currency] | None, Field(min_length=1, max_length=16)] = None,
        include_overdue_summary: bool = True,
        scenario: ScenarioName | None = None,
    ) -> ToolReply[ExposureReport]:
        """Compute and persist validated AR/AP cash-flow exposure from an ERP snapshot."""
        return await call(
            "calc_net_exposure",
            ToolReply[ExposureReport],
            lambda: services.exposures.analyze(
                ExposureRequest(
                    entity_ids=entity_ids,
                    as_of_date=as_of_date,
                    horizon_months=horizon_months,
                    currencies=currencies,
                    include_overdue_summary=include_overdue_summary,
                    scenario=scenario,
                ),
                mcp_principal.get(),
                request_id_var.get(),
            ),
        )

    @server.tool()
    async def get_exposure_report(run_id: UUID) -> ToolReply[ExposureReport]:
        """Get an existing report in the authenticated tenant and entity scope."""
        return await call(
            "get_exposure_report",
            ToolReply[ExposureReport],
            lambda: services.exposures.get(run_id, mcp_principal.get(), request_id_var.get()),
        )

    if kyriba is not None:

        def read_kyriba(entity_id: str, operation: Callable[[], T]) -> T:
            services.queries.entities(mcp_principal.get(), [entity_id])
            return operation()

        @server.tool()
        async def kyriba_list_companies(
            entity_id: EntityCode,
            offset: Offset = 0,
        ) -> ToolReply[KyribaBatch[KyribaCompany]]:
            """Read Kyriba company metadata for an authorized ERP entity."""
            return await call(
                "kyriba_list_companies",
                ToolReply[KyribaBatch[KyribaCompany]],
                lambda: read_kyriba(
                    entity_id, lambda: kyriba.companies(mcp_principal.get(), entity_id, offset)
                ),
            )

        @server.tool()
        async def kyriba_list_cash_flows(
            entity_id: EntityCode,
            query: CashFlowQuery,
        ) -> ToolReply[KyribaBatch[KyribaCashFlow]]:
            """Read dated cash flows for one mapped Kyriba account, without changing ERP exposure."""
            return await call(
                "kyriba_list_cash_flows",
                ToolReply[KyribaBatch[KyribaCashFlow]],
                lambda: read_kyriba(
                    entity_id, lambda: kyriba.cash_flows(mcp_principal.get(), entity_id, query)
                ),
            )

        @server.tool()
        async def kyriba_get_cash_balances(
            entity_id: EntityCode,
            query: CashBalanceQuery,
        ) -> ToolReply[KyribaBatch[KyribaCashBalance]]:
            """Read daily native-currency cash balances for one authorized Kyriba account."""
            return await call(
                "kyriba_get_cash_balances",
                ToolReply[KyribaBatch[KyribaCashBalance]],
                lambda: read_kyriba(
                    entity_id, lambda: kyriba.cash_balances(mcp_principal.get(), entity_id, query)
                ),
            )

        @server.tool()
        async def kyriba_list_third_parties(
            entity_id: EntityCode,
            active_status: ActiveStatus | None = None,
            offset: Offset = 0,
        ) -> ToolReply[KyribaBatch[KyribaThirdParty]]:
            """Read third parties owned by the mapped Kyriba company."""
            return await call(
                "kyriba_list_third_parties",
                ToolReply[KyribaBatch[KyribaThirdParty]],
                lambda: read_kyriba(
                    entity_id,
                    lambda: kyriba.third_parties(
                        mcp_principal.get(), entity_id, active_status, offset
                    ),
                ),
            )

    @server.resource("erp://schema/exposure-v1", mime_type="application/json")
    def exposure_schema() -> str:
        """Return the public business calculation schema to authenticated callers."""
        return json.dumps(ExposureReport.model_json_schema(mode="serialization"))

    @server.prompt()
    def analyze_fx_exposure() -> str:
        """Template for an authorized ERP exposure analysis task."""
        return (
            "Identify the authorized entities, as-of date, horizon and currencies. "
            "Call calc_net_exposure and report its run ID and validation status. Review overdue and "
            "missing due dates separately. Report the scenario and source provenance. Stop on material errors. "
            "Existing hedges are excluded."
        )

    hosts = [host.strip() for host in config.mcp_allowed_hosts.split(",") if host.strip()]
    app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        max_request_body_size=32_768,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[entry for host in hosts for entry in (host, f"{host}:*")],
            allowed_origins=[],
        ),
    )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        try:
            async with server.session_manager.run():
                yield
        finally:
            if kyriba is not None:
                kyriba.close()
            services.close()

    host = Starlette(routes=[Mount("/", app=app)], lifespan=lifespan)
    host.add_middleware(MCPAuth, services=services)
    host.add_middleware(RequestBoundary, accept_trace=True)
    return host


app = create_mcp_app()
