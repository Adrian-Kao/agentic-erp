from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, insert, select

from erp_agent.contracts.exposure import ExposureReport
from erp_agent.database.dataset_tables import run_source_links
from erp_agent.database.tables import items, runs


class RunStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def find(
        self, tenant: str, *, run_id: UUID | None = None, key: str | None = None
    ) -> RowMapping | None:
        if (run_id is None) == (key is None):
            raise ValueError("Specify exactly one run selector")
        selector = runs.c.run_id == run_id if run_id is not None else runs.c.idempotency_key == key
        with self.engine.connect() as conn:
            return (
                conn.execute(select(runs).where(runs.c.tenant_id == tenant, selector))
                .mappings()
                .first()
            )

    def save(
        self,
        conn: Connection,
        report: ExposureReport,
        tenant: str,
        scope: dict[str, UUID],
        request_id: str,
        request_hash: str,
        idempotency_key: str | None,
        *,
        source_documents: tuple[UUID, ...] = (),
    ) -> None:
        conn.execute(
            insert(runs).values(
                run_id=report.run_id,
                tenant_id=tenant,
                entity_scope=sorted(scope),
                as_of_date=report.as_of_date,
                horizon_months=report.horizon_months,
                status=report.status,
                validation_status=report.validation_status,
                input_hash=report.provenance.input_hash,
                data_version=report.provenance.data_version,
                created_at=report.generated_at,
                completed_at=report.generated_at,
                request_id=request_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                report_json=report.model_dump(mode="json"),
            )
        )
        if source_documents:
            conn.execute(
                insert(run_source_links),
                [
                    {"run_id": report.run_id, "scenario_record_id": identifier}
                    for identifier in source_documents
                ],
            )
        if report.exposures:
            conn.execute(
                insert(items),
                [
                    dict(
                        id=uuid4(),
                        run_id=report.run_id,
                        entity_id=scope[item.entity_id],
                        currency=item.currency,
                        period=item.period,
                        ar_open=item.ar_open,
                        ap_open=item.ap_open,
                        net_exposure=item.net_exposure,
                        ar_count=item.source_ar_count,
                        ap_count=item.source_ap_count,
                    )
                    for item in report.exposures
                ],
            )
