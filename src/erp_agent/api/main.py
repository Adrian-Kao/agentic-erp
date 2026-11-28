import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from erp_agent.agent.runtime import AgentRuntime
from erp_agent.agent.skill_loader import SkillMetadata, SkillRegistry
from erp_agent.api.dependencies import KeyDep, PrincipalDep, ServiceDep
from erp_agent.application.query_service import EntityRow
from erp_agent.application.services import Services
from erp_agent.config import Settings
from erp_agent.contracts.agent import AgentRequest, AgentResponse
from erp_agent.contracts.datasets import (
    DatasetManifest,
    DatasetQuality,
    ExposureLineage,
    ScenarioSummary,
)
from erp_agent.contracts.errors import ErrorResponse, ServiceError
from erp_agent.contracts.exposure import ExposureReport
from erp_agent.contracts.messages import TaskMessage, TaskResult
from erp_agent.contracts.requests import ExposureRequest
from erp_agent.database.repository import QueryLimitExceeded
from erp_agent.observability.logging import RequestBoundary, event

Key = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"),
]


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = Services(config)
        app.state.services = services
        try:
            app.state.skills = SkillRegistry()
            app.state.runtime = AgentRuntime(services, app.state.skills)
            yield
        finally:
            services.close()

    logging.getLogger("erp_agent").setLevel(config.log_level.upper())
    app = FastAPI(title="ERP Agent", version="1.0.0", lifespan=lifespan)
    app.add_middleware(RequestBoundary)
    router = APIRouter(
        prefix="/v1",
        responses={
            status: {"model": ErrorResponse}
            for status in (400, 401, 403, 404, 409, 413, 422, 500, 503)
        },
    )

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            exc.response(request.state.request_id).model_dump(), status_code=exc.status
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        body = ErrorResponse(
            code="INVALID_REQUEST",
            message="Request parameters are invalid",
            request_id=request.state.request_id,
            details=[{"type": str(e["type"])} for e in exc.errors()[:10]],
        )
        return JSONResponse(body.model_dump(), status_code=422)

    @app.exception_handler(QueryLimitExceeded)
    async def too_many(request: Request, exc: QueryLimitExceeded) -> JSONResponse:
        return await service_error(request, ServiceError("QUERY_LIMIT", str(exc), 422))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        event("database_error", error_type=type(exc).__name__)
        return await service_error(
            request,
            ServiceError("DATABASE_UNAVAILABLE", "Database unavailable", 503, retryable=True),
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return await service_error(
            request, ServiceError("HTTP_ERROR", "HTTP request failed", exc.status_code)
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", responses={503: {"model": ErrorResponse}})
    def ready(services: ServiceDep) -> dict[str, str]:
        services.ready()
        return {"status": "ready"}

    @router.get("/erp/entities")
    def list_entities(services: ServiceDep, principal: PrincipalDep) -> list[EntityRow]:
        return services.queries.entities(principal)

    @router.get("/erp/schema")
    def schema(
        services: ServiceDep, principal: PrincipalDep, topic: str | None = None
    ) -> dict[str, Any]:
        return services.queries.schema(topic)

    @router.post("/exposures/analyze")
    def analyze(
        body: ExposureRequest,
        request: Request,
        services: ServiceDep,
        principal: PrincipalDep,
        key: Key = None,
    ) -> ExposureReport:
        return services.exposures.analyze(body, principal, request.state.request_id, key)

    @router.get("/exposures/{run_id}")
    def get_report(
        run_id: UUID, request: Request, services: ServiceDep, principal: PrincipalDep
    ) -> ExposureReport:
        return services.exposures.get(run_id, principal, request.state.request_id)

    @router.get("/datasets/manifest")
    def datasets_manifest(services: ServiceDep, principal: PrincipalDep) -> DatasetManifest:
        return services.datasets.manifest(principal)

    @router.get("/datasets/quality")
    def datasets_quality(services: ServiceDep, principal: PrincipalDep) -> DatasetQuality:
        return services.datasets.quality()

    @router.get("/scenarios")
    def list_scenarios(services: ServiceDep, principal: PrincipalDep) -> list[ScenarioSummary]:
        return services.datasets.scenarios(principal)

    @router.get("/exposures/{run_id}/lineage")
    def get_lineage(
        run_id: UUID,
        request: Request,
        services: ServiceDep,
        principal: PrincipalDep,
        include_records: bool = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> ExposureLineage:
        report = services.exposures.get(run_id, principal, request.state.request_id)
        return services.datasets.lineage(
            report, principal, include_records=include_records, limit=limit
        )

    @router.post("/mas/tasks")
    def run_task(
        body: TaskMessage,
        request: Request,
        services: ServiceDep,
        principal: PrincipalDep,
        key: Key = None,
    ) -> TaskResult:
        if principal.actor_type != "service":
            raise ServiceError("SERVICE_REQUIRED", "A service principal is required", 403)
        report = services.exposures.analyze(
            body.parameters, principal, request.state.request_id, key
        )
        return TaskResult(
            task_id=body.task_id, status=report.status, run_id=report.run_id, result=report
        )

    @router.get("/agent/skills")
    def list_skills(request: Request, principal: PrincipalDep) -> list[SkillMetadata]:
        skills: SkillRegistry = request.app.state.skills
        return skills.list()

    @router.post("/agent/run")
    async def run_agent(
        body: AgentRequest, request: Request, principal: PrincipalDep, key: KeyDep
    ) -> AgentResponse:
        runtime: AgentRuntime = request.app.state.runtime
        return await runtime.run(body, principal, key or "", request.state.request_id)

    app.include_router(router)
    return app


app = create_app()
