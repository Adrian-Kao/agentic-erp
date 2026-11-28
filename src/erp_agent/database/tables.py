from decimal import Decimal
from typing import TypedDict
from uuid import UUID

from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    DateTime,
    Dialect,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy import (
    Uuid as NativeUuid,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.types import TypeEngine


class ExactMoney(TypeDecorator[Decimal]):
    """Native DECIMAL/NUMERIC and lossless text for isolated SQLite tests."""

    impl = Numeric(20, 4)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine:
        return dialect.type_descriptor(String(22) if dialect.name == "sqlite" else self.impl)

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Decimal | str | None:
        if value is None:
            return None
        amount = Decimal(value)
        return format(amount, "f") if dialect.name == "sqlite" else amount

    def process_result_value(self, value: str | Decimal | None, dialect: Dialect) -> Decimal | None:
        return Decimal(value) if value is not None else None


class Uuid(TypeDecorator[UUID]):
    impl = NativeUuid
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine:
        return dialect.type_descriptor(CHAR(36) if dialect.name == "mysql" else NativeUuid())

    def process_bind_param(self, value: UUID | None, dialect: Dialect) -> UUID | str | None:
        return str(value) if value is not None and dialect.name == "mysql" else value

    def process_result_value(self, value: UUID | str | None, dialect: Dialect) -> UUID | None:
        return UUID(value) if isinstance(value, str) else value


TIME_UTC = DateTime(timezone=True).with_variant(DATETIME(fsp=6), "mysql")
CURRENCY = String(3).with_variant(CHAR(3), "mysql")


class _MySQLOptions(TypedDict):
    mysql_engine: str
    mysql_charset: str
    mysql_collate: str


MYSQL_OPTIONS: _MySQLOptions = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_as_cs",
}

metadata = MetaData()
entities = Table(
    "legal_entities",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("entity_code", String(32), nullable=False),
    Column("name", String(200), nullable=False),
    Column("functional_currency", CURRENCY, nullable=False),
    Column("is_active", Boolean, nullable=False),
    UniqueConstraint("tenant_id", "entity_code"),
    UniqueConstraint("tenant_id", "id"),
    **MYSQL_OPTIONS,
)


def invoice_table(name: str) -> Table:
    return Table(
        name,
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", String(64), nullable=False),
        Column("entity_id", Uuid, nullable=False),
        Column("document_no", String(64), nullable=False),
        Column("currency", CURRENCY, nullable=False),
        Column("issue_date", Date, nullable=False),
        Column("due_date", Date),
        Column("original_amount", ExactMoney(), nullable=False),
        Column("status", String(16), nullable=False),
        Column("created_at", TIME_UTC, nullable=False),
        Column("updated_at", TIME_UTC, nullable=False),
        ForeignKeyConstraint(["tenant_id", "entity_id"], [entities.c.tenant_id, entities.c.id]),
        UniqueConstraint("tenant_id", "entity_id", "document_no"),
        UniqueConstraint("tenant_id", "id", "currency"),
        Index(f"ix_{name}_scope", "tenant_id", "entity_id", "due_date", "status"),
        **MYSQL_OPTIONS,
    )


ar = invoice_table("ar_invoices")
ap = invoice_table("ap_invoices")
allocations = Table(
    "payment_allocations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("document_type", String(2), nullable=False),
    Column("document_id", Uuid, nullable=False),
    Column("currency", CURRENCY, nullable=False),
    Column("amount", ExactMoney(), nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("status", String(16), nullable=False),
    Column(
        "ar_document_id",
        Uuid,
        Computed("CASE WHEN document_type = 'AR' THEN document_id END", persisted=True),
    ),
    Column(
        "ap_document_id",
        Uuid,
        Computed("CASE WHEN document_type = 'AP' THEN document_id END", persisted=True),
    ),
    ForeignKeyConstraint(
        ["tenant_id", "ar_document_id", "currency"], [ar.c.tenant_id, ar.c.id, ar.c.currency]
    ),
    ForeignKeyConstraint(
        ["tenant_id", "ap_document_id", "currency"], [ap.c.tenant_id, ap.c.id, ap.c.currency]
    ),
    CheckConstraint("document_type IN ('AR', 'AP')", name="payment_document_type"),
    CheckConstraint("status IN ('POSTED', 'VOID')", name="payment_status"),
    Index(
        "ix_allocations_document",
        "tenant_id",
        "document_type",
        "document_id",
        "effective_date",
        "status",
    ),
    **MYSQL_OPTIONS,
)
runs = Table(
    "exposure_runs",
    metadata,
    Column("run_id", Uuid, primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("entity_scope", JSON, nullable=False),
    Column("as_of_date", Date, nullable=False),
    Column("horizon_months", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("validation_status", String(16), nullable=False),
    Column("input_hash", String(80), nullable=False),
    Column("data_version", String(80), nullable=False),
    Column("created_at", TIME_UTC, nullable=False),
    Column("completed_at", TIME_UTC, nullable=False),
    Column("request_id", String(64), nullable=False),
    Column("idempotency_key", String(128)),
    Column("request_hash", String(80), nullable=False),
    Column("report_json", JSON, nullable=False),
    UniqueConstraint("tenant_id", "idempotency_key"),
    **MYSQL_OPTIONS,
)
items = Table(
    "exposure_items",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("run_id", Uuid, ForeignKey(runs.c.run_id), nullable=False),
    Column("entity_id", Uuid, ForeignKey(entities.c.id), nullable=False),
    Column("currency", CURRENCY, nullable=False),
    Column("period", String(7), nullable=False),
    Column("ar_open", ExactMoney(), nullable=False),
    Column("ap_open", ExactMoney(), nullable=False),
    Column("net_exposure", ExactMoney(), nullable=False),
    Column("ar_count", Integer, nullable=False),
    Column("ap_count", Integer, nullable=False),
    UniqueConstraint("run_id", "entity_id", "currency", "period"),
    **MYSQL_OPTIONS,
)
audit = Table(
    "audit_events",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("run_id", Uuid, ForeignKey(runs.c.run_id)),
    Column("tenant_id", String(64), nullable=False),
    Column("actor_type", String(16), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("operation", String(40), nullable=False),
    Column("result", String(32), nullable=False),
    Column("metadata_json", JSON, nullable=False),
    Column("created_at", TIME_UTC, nullable=False),
    Index("ix_audit_tenant_created", "tenant_id", "created_at"),
    **MYSQL_OPTIONS,
)
