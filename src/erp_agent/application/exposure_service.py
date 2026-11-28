from uuid import UUID

from sqlalchemy.exc import IntegrityError

from erp_agent.contracts.errors import ServiceError
from erp_agent.contracts.exposure import ExposureReport
from erp_agent.contracts.requests import ExposureRequest
from erp_agent.database.repository import ERPAdapter
from erp_agent.domain.exposure_calculator import calculate, content_hash
from erp_agent.domain.validation import validate_exposure
from erp_agent.memory.run_store import RunStore
from erp_agent.observability.audit import write_audit
from erp_agent.observability.logging import event
from erp_agent.security.authorization import Principal


class ExposureService:
    def __init__(self, repository: ERPAdapter, store: RunStore) -> None:
        self.repository = repository
        self.store = store

    def get(self, run_id: UUID, principal: Principal, request_id: str) -> ExposureReport:
        row = self.store.find(principal.tenant_id, run_id=run_id)
        if row is None or not set(row.entity_scope) <= principal.entity_ids:
            raise ServiceError("REPORT_NOT_FOUND", "Exposure report not found", 404)
        with self.store.engine.begin() as conn:
            write_audit(conn, principal, "report_read", "OK", request_id, run_id=run_id)
        return ExposureReport.model_validate(row.report_json)

    def analyze(
        self,
        request: ExposureRequest,
        principal: Principal,
        request_id: str,
        key: str | None = None,
    ) -> ExposureReport:
        scope = principal.scope(request.entity_ids)
        request_hash = content_hash(
            request.model_dump(
                mode="json", exclude={"scenario"} if request.scenario is None else None
            )
        )

        def replay() -> ExposureReport | None:
            if key is None:
                return None
            row = self.store.find(principal.tenant_id, key=key)
            if row is None:
                return None
            if not set(row.entity_scope) <= principal.entity_ids:
                raise ServiceError("ENTITY_FORBIDDEN", "Idempotency key is outside your scope", 403)
            if row.request_hash != request_hash:
                raise ServiceError(
                    "IDEMPOTENCY_CONFLICT", "Key was used with different parameters", 409
                )
            return self.get(row.run_id, principal, request_id)

        prior = replay()
        if prior is not None:
            return prior
        snapshot = self.repository.snapshot(
            principal.tenant_id,
            scope,
            request.as_of_date,
            tuple(request.currencies or ()),
            request.scenario,
        )
        entity_map = {entity.entity_code: entity.id for entity in snapshot.entities}
        if set(entity_map) != set(scope):
            raise ServiceError(
                "ENTITY_FORBIDDEN", "One or more entities are outside your scope", 403
            )
        report = calculate(snapshot, request)
        validate_exposure(report, snapshot, request)
        try:
            with self.store.engine.begin() as conn:
                self.store.save(
                    conn,
                    report,
                    principal.tenant_id,
                    entity_map,
                    request_id,
                    request_hash,
                    key,
                    source_documents=tuple(
                        doc.id for doc in snapshot.invoices if doc.origin is not None
                    ),
                )
                write_audit(
                    conn,
                    principal,
                    "analyze",
                    report.status,
                    request_id,
                    run_id=report.run_id,
                    source_rows=len(snapshot.invoices),
                    issue_count=len(report.data_quality_issues),
                )
        except IntegrityError:
            prior = replay()
            if prior is None:
                raise
            return prior
        event(
            "report_saved",
            run_id=str(report.run_id),
            result=report.status,
            source_rows=len(snapshot.invoices),
            issue_count=len(report.data_quality_issues),
        )
        return report
