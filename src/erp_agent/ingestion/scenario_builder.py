import random
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import Field, model_validator
from sqlalchemy import Connection, Engine, Row, insert, select

from erp_agent.contracts.requests import InputModel
from erp_agent.database.dataset_tables import (
    active_scenarios,
    batches,
    current_source,
    journals,
    scenario_documents,
    source_links,
    versions,
)
from erp_agent.database.tables import allocations, ap, ar, entities
from erp_agent.domain.date_buckets import month_window
from erp_agent.ingestion.load_mysql import bulk_insert, set_pointer
from erp_agent.ingestion.source_manifest import SourceError
from erp_agent.ingestion.source_reader import content_hash

SCENARIO_PATH = Path("data/scenarios/aster_tw_v1.yaml")
TENANTS = (("DEMO_A", "TW01"), ("DEMO_A", "TW02"), ("DEMO_B", "TW01"))
ZERO = Decimal("0.0000")
SCALE = Decimal("0.0001")


class ScenarioConfig(InputModel):
    scenario_id: Literal["aster-treasury-v1"] = "aster-treasury-v1"
    version: str = Field(default="1", pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    seed: int = Field(default=113306018, ge=0, le=2_147_483_647)
    as_of_date: date
    first_due_month: date
    months: Literal[6] = 6
    currencies: tuple[Literal["USD"], Literal["EUR"], Literal["JPY"]]
    rows_per_currency_month_and_side: int = Field(ge=6, le=20)

    @model_validator(mode="after")
    def check_calendar(self) -> Self:
        if not date(1900, 2, 1) <= self.as_of_date <= self.first_due_month <= date(9998, 12, 1):
            raise ValueError("Scenario dates must define a supported future window")
        if self.first_due_month.day != 1:
            raise ValueError("Scenario must begin on a calendar month boundary")
        return self


@dataclass(frozen=True)
class ScenarioDocument:
    tenant: str
    entity: str
    kind: Literal["AR", "AP"]
    amount: Decimal
    currency: str
    due: date | None
    source: Row | None = None
    status: str = "OPEN"
    payment: Decimal = ZERO
    external_ref: str | None = None


def read_scenario(path: Path = SCENARIO_PATH) -> ScenarioConfig:
    return ScenarioConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _entities(conn: Connection) -> dict[tuple[str, str], UUID]:
    result: dict[tuple[str, str], UUID] = {}
    for tenant, code in TENANTS:
        existing = conn.scalar(
            select(entities.c.id).where(
                entities.c.tenant_id == tenant, entities.c.entity_code == code
            )
        )
        if existing is None:
            existing = uuid5(NAMESPACE_URL, f"erp-demo-v1/{tenant}/{code}")
            conn.execute(
                insert(entities).values(
                    id=existing,
                    tenant_id=tenant,
                    entity_code=code,
                    name="Aster Electronics Co., Ltd. (fictional)"
                    if (tenant, code) == TENANTS[0]
                    else f"Aster isolation fixture {tenant} {code}",
                    functional_currency="TWD",
                    is_active=True,
                )
            )
        result[tenant, code] = existing
    return result


def _derive(conn: Connection, batch: UUID, cfg: ScenarioConfig) -> list[ScenarioDocument]:
    documents: list[ScenarioDocument] = []
    rng = random.Random(cfg.seed)
    total = cfg.months * len(cfg.currencies) * cfg.rows_per_currency_month_and_side
    loc = source_links.c
    for kind in ("AR", "AP"):
        records = conn.execute(
            select(
                journals.c.id,
                journals.c.debit,
                journals.c.credit,
                journals.c.currency,
                loc.original_import_record_id,
                loc.source_file,
                loc.source_sheet,
                loc.source_row_number,
                loc.source_row_hash,
            )
            .join(source_links, loc.canonical_record_id == journals.c.id)
            .where(
                journals.c.kind == kind,
                journals.c.import_batch_id == batch,
                loc.import_batch_id == batch,
                loc.scenario_record_id.is_(None),
            )
            .order_by(journals.c.id)
        ).all()
        if len(records) < total + 1:
            raise SourceError("SCENARIO_SOURCE_COVERAGE: insufficient mapped journal rows")
        selected = iter(rng.sample(records, total + 1))
        for month in range(cfg.months):
            _, start = month_window(cfg.first_due_month, month)
            for currency in cfg.currencies:
                for i in range(cfg.rows_per_currency_month_and_side):
                    source = next(selected)
                    amount = abs((source.debit or ZERO) - (source.credit or ZERO))
                    paid = (amount / Decimal(4)).quantize(SCALE) if i % 4 == 0 else ZERO
                    due = start + timedelta(days=rng.randrange(0, 25))
                    documents.append(
                        ScenarioDocument(
                            "DEMO_A", "TW01", kind, amount, currency, due, source, payment=paid
                        )
                    )
        source = next(selected)
        tenant, entity = ("DEMO_A", "TW02") if kind == "AP" else ("DEMO_B", "TW01")
        amount = abs((source.debit or ZERO) - (source.credit or ZERO))
        documents.append(
            ScenarioDocument(tenant, entity, kind, amount, "USD", cfg.first_due_month, source)
        )
    documents.extend(
        [
            ScenarioDocument(
                "DEMO_A", "TW01", "AR", Decimal("2250"), "USD", cfg.as_of_date - timedelta(days=3)
            ),
            ScenarioDocument(
                "DEMO_A",
                "TW01",
                "AP",
                Decimal("2000"),
                "EUR",
                cfg.first_due_month,
                status="SETTLED",
                payment=Decimal("2000"),
            ),
            ScenarioDocument(
                "DEMO_A",
                "TW01",
                "AP",
                Decimal("440"),
                "JPY",
                cfg.first_due_month,
                status="CANCELLED",
            ),
        ]
    )
    return documents


def _faults(cfg: ScenarioConfig) -> list[ScenarioDocument]:
    common = ("DEMO_A", "TW01")
    return [
        ScenarioDocument(*common, "AR", Decimal("120"), "USD", None),
        ScenarioDocument(*common, "AR", Decimal("140"), "", cfg.first_due_month),
        ScenarioDocument(
            *common, "AR", Decimal("160"), "USD", cfg.first_due_month, status="SETTLED"
        ),
        ScenarioDocument(
            *common, "AR", Decimal("180"), "USD", cfg.first_due_month, payment=Decimal("181")
        ),
        ScenarioDocument(
            *common, "AP", Decimal("200"), "USD", cfg.first_due_month, external_ref="DUPLICATE"
        ),
        ScenarioDocument(
            *common, "AP", Decimal("220"), "USD", cfg.first_due_month, external_ref="DUPLICATE"
        ),
    ]


def _save_documents(
    conn: Connection, version: UUID, batch: UUID, cfg: ScenarioConfig, docs: list[ScenarioDocument]
) -> None:
    entity_map = _entities(conn)
    posted_at = datetime.combine(cfg.as_of_date, datetime.min.time(), tzinfo=UTC)
    invoices: dict[str, list[dict[str, Any]]] = {"AR": [], "AP": []}
    payments: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    for i, doc in enumerate(docs):
        doc_id = uuid5(version, f"{doc.tenant}/{doc.entity}/{doc.kind}/{i}")
        entity_id = entity_map[doc.tenant, doc.entity]
        source = doc.source
        invoices[doc.kind].append(
            {
                "id": doc_id,
                "tenant_id": doc.tenant,
                "entity_id": entity_id,
                "document_no": f"AST-{version.hex}-{i}",
                "currency": doc.currency,
                "issue_date": cfg.as_of_date - timedelta(days=20),
                "due_date": doc.due,
                "original_amount": doc.amount,
                "status": doc.status,
                "created_at": posted_at,
                "updated_at": posted_at,
            }
        )
        refs = ["AST-ENTITY", "AST-DATES", "AST-STATUS"]
        fields = {
            "entity_id": "DEMO_OVERLAY",
            "status": "DEMO_ASSIGNED",
            "due_date": "DEMO_ASSIGNED",
            "currency": "SOURCE_CURRENCY"
            if source is not None and source.currency == doc.currency
            else "DEMO_DENOMINATION",
            "original_amount": "JOURNAL_MAGNITUDE" if source is not None else "DEMO_SYNTHETIC",
            "open_balance": "DEMO_POSTED_ALLOCATIONS",
        }
        if source is not None:
            refs.append("AST-MAGNITUDE")
            if doc.currency != source.currency:
                refs.append("AST-RE-DENOMINATION")
            lineage.append(
                {
                    "id": uuid5(doc_id, "source-link"),
                    "import_batch_id": batch,
                    "original_import_record_id": source.original_import_record_id,
                    "canonical_record_id": source.id,
                    "scenario_record_id": doc_id,
                    **{
                        key: getattr(source, key)
                        for key in (
                            "source_file",
                            "source_sheet",
                            "source_row_number",
                            "source_row_hash",
                        )
                    },
                }
            )
        if doc.payment or (source is not None and i % 17 == 1):
            refs.append("AST-PAYMENTS")
        if doc.payment:
            payments.append(
                {
                    "id": uuid5(doc_id, "posted"),
                    "tenant_id": doc.tenant,
                    "document_type": doc.kind,
                    "document_id": doc_id,
                    "currency": doc.currency,
                    "amount": doc.payment,
                    "effective_date": cfg.as_of_date,
                    "status": "POSTED",
                }
            )
        if source is not None and i % 17 == 1:
            for label, days, status in (("future", 10, "POSTED"), ("void", -2, "VOID")):
                payments.append(
                    {
                        "id": uuid5(doc_id, label),
                        "tenant_id": doc.tenant,
                        "document_type": doc.kind,
                        "document_id": doc_id,
                        "currency": doc.currency,
                        "amount": (doc.amount / 10).quantize(SCALE),
                        "effective_date": cfg.as_of_date + timedelta(days=days),
                        "status": status,
                    }
                )
        details.append(
            {
                "id": doc_id,
                "version_id": version,
                "tenant_id": doc.tenant,
                "entity_id": entity_id,
                "document_type": doc.kind,
                "source_kind": (
                    "DEMO_DERIVED_FROM_MICROSOFT" if source is not None else "DEMO_SYNTHETIC"
                ),
                "source_record_ref": source.original_import_record_id
                if source is not None
                else None,
                "canonical_record_id": source.id if source is not None else None,
                "source_transform_note": "Journal magnitude with scenario dates, status and denomination"
                if source is not None
                else "Isolated synthetic scenario case",
                "assumption_ids": refs,
                "field_lineage_json": fields,
                "open_balance_as_of": ZERO
                if doc.status == "CANCELLED"
                else doc.amount - doc.payment,
                "external_document_no": doc.external_ref,
                "bank_receipt_date": doc.due + timedelta(days=(i % 4) + 1)
                if doc.due is not None
                else None,
            }
        )
    for table, name in ((ar, "AR"), (ap, "AP")):
        bulk_insert(conn, table, invoices[name])
    bulk_insert(conn, allocations, payments)
    bulk_insert(conn, scenario_documents, details)
    bulk_insert(conn, source_links, lineage)


def build_scenarios(engine: Engine, cfg: ScenarioConfig) -> dict[str, dict[str, Any]]:
    payload = cfg.model_dump(mode="json")
    result: dict[str, dict[str, Any]] = {}
    with engine.begin() as conn:
        batch = conn.scalar(
            select(current_source.c.import_batch_id)
            .where(current_source.c.name == "financebenchmark")
            .with_for_update()
        )
        if batch is None:
            raise SourceError("SOURCE_DATA_UNAVAILABLE: import Microsoft FinanceBenchmark first")
        manifest = conn.scalar(select(batches.c.manifest_json).where(batches.c.id == batch))
        if not manifest or not manifest.get("pinned_blob_verified"):
            raise SourceError("MICROSOFT_IMPORT_NOT_VERIFIED")
        config_hash = content_hash({"generator": "aster-v1", "config": payload})
        for name in ("baseline", "data_quality_faults"):
            version = uuid5(batch, f"{name}/{config_hash}")
            stats = conn.scalar(select(versions.c.stats_json).where(versions.c.id == version))
            if stats is None:
                docs = _derive(conn, batch, cfg) if name == "baseline" else _faults(cfg)
                by_month = Counter(
                    doc.due.strftime("%Y-%m")
                    for doc in docs
                    if doc.due is not None
                    and (doc.tenant, doc.entity) == TENANTS[0]
                    and doc.status == "OPEN"
                    and doc.due >= cfg.first_due_month
                )
                stats = {
                    "documents": len(docs),
                    "scenario_rows_derived": sum(d.source is not None for d in docs),
                    "synthetic_only_rows": sum(d.source is None for d in docs),
                    "dated_main_documents_by_month": dict(sorted(by_month.items())),
                }
                conn.execute(
                    insert(versions).values(
                        id=version,
                        import_batch_id=batch,
                        scenario=name,
                        scenario_id=cfg.scenario_id,
                        scenario_version=cfg.version,
                        scenario_seed=cfg.seed,
                        config_hash=config_hash,
                        as_of_date=cfg.as_of_date,
                        config_json=payload,
                        stats_json=stats,
                        created_at=datetime.now(UTC),
                    )
                )
                _save_documents(conn, version, batch, cfg, docs)
            set_pointer(conn, active_scenarios, "scenario", name, version_id=version)
            result[name] = {"version_id": str(version), **stats}
    return result


def register_fixture(engine: Engine) -> dict[str, Any]:
    version = uuid5(NAMESPACE_URL, "erp-demo-v1/fixture-only")
    cutoff = date(2026, 11, 27)
    with engine.begin() as conn:
        stats = conn.scalar(select(versions.c.stats_json).where(versions.c.id == version))
        if stats is None:
            rows = []
            for table, kind in ((ar, "AR"), (ap, "AP")):
                for doc in conn.execute(
                    select(table).where(
                        table.c.tenant_id.in_([tenant for tenant, _ in TENANTS]),
                        table.c.document_no.like("DEMO-%"),
                    )
                ):
                    paid = sum(
                        conn.scalars(
                            select(allocations.c.amount).where(
                                allocations.c.document_id == doc.id,
                                allocations.c.document_type == kind,
                                allocations.c.tenant_id == doc.tenant_id,
                                allocations.c.status == "POSTED",
                                allocations.c.effective_date <= cutoff,
                            )
                        ),
                        ZERO,
                    )
                    rows.append(
                        {
                            "id": doc.id,
                            "version_id": version,
                            "tenant_id": doc.tenant_id,
                            "entity_id": doc.entity_id,
                            "document_type": kind,
                            "source_kind": "DEMO_SYNTHETIC",
                            "source_transform_note": "Golden fixture seeded separately from Microsoft data",
                            "assumption_ids": ["GOLDEN_FIXTURE"],
                            "field_lineage_json": {"all": "DEMO_SYNTHETIC"},
                            "open_balance_as_of": ZERO
                            if doc.status == "CANCELLED"
                            else doc.original_amount - paid,
                            "external_document_no": doc.document_no,
                        }
                    )
            if not rows:
                raise SourceError("FIXTURE_SOURCE_MISSING: seed the golden fixture first")
            stats = {
                "documents": len(rows),
                "scenario_rows_derived": 0,
                "synthetic_only_rows": len(rows),
            }
            config = {"currencies": ["USD", "EUR", "JPY"], "fixture": "golden-v1"}
            conn.execute(
                insert(versions).values(
                    id=version,
                    scenario="fixture_only",
                    scenario_id="aster-treasury-v1",
                    scenario_version="1",
                    scenario_seed=0,
                    config_hash=content_hash(config),
                    as_of_date=cutoff,
                    config_json=config,
                    stats_json=stats,
                    created_at=datetime.now(UTC),
                )
            )
            bulk_insert(conn, scenario_documents, rows)
        set_pointer(conn, active_scenarios, "scenario", "fixture_only", version_id=version)
    return {"version_id": str(version), **stats}
