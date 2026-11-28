from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import Connection, Engine, and_, select

from erp_agent.contracts.errors import ServiceError
from erp_agent.contracts.requests import CURRENCIES, ScenarioName
from erp_agent.database.dataset_tables import (
    active_scenarios,
    batches,
    scenario_documents,
    versions,
)
from erp_agent.database.named_queries import INVOICE_TABLES, DocumentType
from erp_agent.database.tables import allocations, entities

MAX_ROWS = 10_000


class QueryLimitExceeded(ValueError):
    """The authorized snapshot exceeds a configured row budget."""


@dataclass(frozen=True)
class Entity:
    id: UUID
    entity_code: str
    name: str
    functional_currency: str


@dataclass(frozen=True)
class DocumentOrigin:
    source_kind: str
    source_record_ref: UUID | None
    canonical_record_id: UUID | None
    assumptions: tuple[str, ...]
    external_document_no: str | None


@dataclass(frozen=True)
class Invoice:
    id: UUID
    entity_id: UUID
    kind: DocumentType
    currency: str
    issue_date: date
    due_date: date | None
    original_amount: Decimal
    status: str
    origin: DocumentOrigin | None = None


@dataclass(frozen=True)
class Allocation:
    id: UUID
    document_id: UUID
    kind: DocumentType
    currency: str
    amount: Decimal
    effective_date: date
    status: str


@dataclass(frozen=True)
class DatasetSnapshot:
    version_id: UUID
    scenario: str
    scenario_id: str
    scenario_version: str
    scenario_seed: int
    config_hash: str
    source_system: str
    upstream_repo: str | None = None
    upstream_commit: str | None = None
    source_sha256: str | None = None
    import_batch_id: UUID | None = None
    source_rows_read: int = 0
    canonical_mapped: int = 0
    quarantined: int = 0
    missing_required_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    entities: tuple[Entity, ...]
    invoices: tuple[Invoice, ...]
    allocations: tuple[Allocation, ...]
    source_system: str = "ERP_DEMO"
    data_version: str = "demo-seed-v1"
    dataset: DatasetSnapshot | None = None


class ERPAdapter(Protocol):
    def list_entities(self, tenant: str, codes: tuple[str, ...]) -> tuple[Entity, ...]: ...

    def snapshot(
        self,
        tenant: str,
        codes: tuple[str, ...],
        as_of: date,
        currencies: tuple[str, ...] = (),
        scenario: ScenarioName | None = None,
    ) -> Snapshot: ...


def _entities(conn: Connection, tenant: str, codes: tuple[str, ...]) -> tuple[Entity, ...]:
    query = (
        select(
            entities.c.id, entities.c.entity_code, entities.c.name, entities.c.functional_currency
        )
        .where(
            entities.c.tenant_id == tenant,
            entities.c.is_active.is_(True),
            entities.c.entity_code.in_(codes),
        )
        .order_by(entities.c.entity_code)
    )
    return tuple(Entity(*row) for row in conn.execute(query))


def dataset_snapshot(conn: Connection, name: ScenarioName) -> DatasetSnapshot:
    row = conn.execute(
        select(
            versions,
            batches.c.upstream_repo,
            batches.c.upstream_commit,
            batches.c.source_sha256,
            batches.c.source_rows,
            batches.c.canonical_rows,
            batches.c.quarantined_rows,
            batches.c.manifest_json,
        )
        .select_from(active_scenarios.join(versions).outerjoin(batches))
        .where(
            active_scenarios.c.scenario == name,
        )
    ).first()
    if row is None:
        raise ServiceError(
            "SCENARIO_UNAVAILABLE", "The requested dataset scenario is not available", 503
        )
    system = {
        "baseline": "FINANCEBENCHMARK_DERIVED_DEMO",
        "data_quality_faults": "DEMO_SYNTHETIC_FAULTS",
        "fixture_only": "DEMO_SYNTHETIC_FIXTURE",
    }[name]
    semantics = (row.manifest_json or {}).get("business_semantics", {})
    return DatasetSnapshot(
        version_id=row.id,
        scenario=row.scenario,
        scenario_id=row.scenario_id,
        scenario_version=row.scenario_version,
        scenario_seed=row.scenario_seed,
        config_hash=row.config_hash,
        source_system=system,
        upstream_repo=row.upstream_repo,
        upstream_commit=row.upstream_commit,
        source_sha256=row.source_sha256,
        import_batch_id=row.import_batch_id,
        source_rows_read=row.source_rows or 0,
        canonical_mapped=row.canonical_rows or 0,
        quarantined=row.quarantined_rows or 0,
        missing_required_fields=tuple(
            sorted(
                field
                for field, entry in semantics.items()
                if entry["status"] in {"MISSING", "UNVERIFIED"}
            )
        ),
    )


class SQLAlchemyERPRepository:
    def __init__(self, engine: Engine, default_scenario: ScenarioName | None = None) -> None:
        self.engine = engine
        self.default_scenario: ScenarioName | None = default_scenario or (
            "baseline" if engine.dialect.name == "mysql" else None
        )

    def list_entities(self, tenant: str, codes: tuple[str, ...]) -> tuple[Entity, ...]:
        with self.engine.connect() as conn:
            return _entities(conn, tenant, codes)

    def snapshot(
        self,
        tenant: str,
        codes: tuple[str, ...],
        as_of: date,
        currencies: tuple[str, ...] = (),
        scenario: ScenarioName | None = None,
    ) -> Snapshot:
        invoices: list[Invoice] = []
        payments: list[Allocation] = []
        with self.engine.begin() as conn:
            scenario = scenario or self.default_scenario
            dataset = dataset_snapshot(conn, scenario) if scenario else None
            scoped = _entities(conn, tenant, codes)
            version_docs = scenario_documents.c
            for kind in ("AR", "AP"):
                table = INVOICE_TABLES[kind]
                scope = [
                    table.c.tenant_id == tenant,
                    table.c.entity_id.in_([e.id for e in scoped]),
                    table.c.issue_date <= as_of,
                ]
                if currencies:
                    scope.append(
                        table.c.currency.in_(currencies) | table.c.currency.not_in(CURRENCIES)
                    )
                if dataset:
                    allowed = select(version_docs.id).where(
                        version_docs.version_id == dataset.version_id,
                        version_docs.tenant_id == tenant,
                        version_docs.document_type == kind,
                        version_docs.entity_id.in_([entity.id for entity in scoped]),
                    )
                    scope.append(table.c.id.in_(allowed))
                selected = select(table.c.id).where(*scope)
                docs = (
                    select(
                        table.c.id,
                        table.c.entity_id,
                        table.c.currency,
                        table.c.issue_date,
                        table.c.due_date,
                        table.c.original_amount,
                        table.c.status,
                    )
                    .where(*scope)
                    .order_by(table.c.id)
                    .limit(MAX_ROWS + 1 - len(invoices))
                )
                if dataset:
                    docs = docs.add_columns(
                        version_docs.source_kind,
                        version_docs.source_record_ref,
                        version_docs.canonical_record_id,
                        version_docs.assumption_ids,
                        version_docs.external_document_no,
                    ).select_from(
                        table.join(
                            scenario_documents,
                            and_(
                                version_docs.id == table.c.id,
                                version_docs.entity_id == table.c.entity_id,
                                version_docs.version_id == dataset.version_id,
                            ),
                        )
                    )
                for row in conn.execute(docs):
                    origin = (
                        DocumentOrigin(
                            row.source_kind,
                            row.source_record_ref,
                            row.canonical_record_id,
                            tuple(row.assumption_ids),
                            row.external_document_no,
                        )
                        if dataset
                        else None
                    )
                    invoices.append(
                        Invoice(
                            row.id,
                            row.entity_id,
                            kind,
                            row.currency,
                            row.issue_date,
                            row.due_date,
                            row.original_amount,
                            row.status,
                            origin,
                        )
                    )
                if len(invoices) > MAX_ROWS:
                    raise QueryLimitExceeded("Invoice snapshot exceeds the row limit")
                p = allocations.c
                query = (
                    select(p.id, p.document_id, p.currency, p.amount, p.effective_date, p.status)
                    .where(
                        p.tenant_id == tenant,
                        p.document_type == kind,
                        p.document_id.in_(selected),
                        p.effective_date <= as_of,
                    )
                    .order_by(p.id)
                    .limit(MAX_ROWS + 1 - len(payments))
                )
                for row in conn.execute(query):
                    payments.append(
                        Allocation(
                            row.id,
                            row.document_id,
                            kind,
                            row.currency,
                            row.amount,
                            row.effective_date,
                            row.status,
                        )
                    )
                if len(payments) > MAX_ROWS:
                    raise QueryLimitExceeded("Payment snapshot exceeds the row limit")
        if dataset:
            return Snapshot(
                scoped,
                tuple(invoices),
                tuple(payments),
                dataset.source_system,
                f"{dataset.scenario}-{dataset.version_id}",
                dataset,
            )
        return Snapshot(scoped, tuple(invoices), tuple(payments))
