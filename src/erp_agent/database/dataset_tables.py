from sqlalchemy import (
    CHAR,
    JSON,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

from erp_agent.database.tables import (
    CURRENCY,
    MYSQL_OPTIONS,
    TIME_UTC,
    ExactMoney,
    Uuid,
    entities,
    runs,
)

dataset_metadata = MetaData()

batches = Table(
    "dataset_import_batches",
    dataset_metadata,
    Column("id", Uuid, primary_key=True),
    Column("upstream_repo", String(255), nullable=False),
    Column("upstream_commit", CHAR(40), nullable=False),
    Column("source_sha256", CHAR(64), nullable=False),
    Column("mapping_hash", CHAR(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("manifest_json", JSON, nullable=False),
    Column("source_rows", Integer, nullable=False),
    Column("canonical_rows", Integer, nullable=False),
    Column("quarantined_rows", Integer, nullable=False),
    Column("error_code", String(64)),
    Column("created_at", TIME_UTC, nullable=False),
    Column("completed_at", TIME_UTC),
    CheckConstraint(
        "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')", name="ck_dataset_status"
    ),
    UniqueConstraint("upstream_commit", "source_sha256", name="uq_dataset_source"),
    **MYSQL_OPTIONS,
)


def _raw(name: str) -> Table:
    return Table(
        name,
        dataset_metadata,
        Column("id", Uuid, primary_key=True),
        Column("import_batch_id", Uuid, ForeignKey(batches.c.id), nullable=False),
        Column("source_file", String(255), nullable=False),
        Column("source_sheet", String(128), nullable=False),
        Column("source_row_number", Integer, nullable=False),
        Column("source_row_hash", CHAR(64), nullable=False),
        Column("raw_json", JSON, nullable=False),
        Column("imported_at", TIME_UTC, nullable=False),
        UniqueConstraint(
            "import_batch_id",
            "source_file",
            "source_sheet",
            "source_row_number",
            name=f"uq_{name}_source",
        ),
        **MYSQL_OPTIONS,
    )


raw_tables = {
    "customers": _raw("raw_customers"),
    "vendors": _raw("raw_vendors"),
    "ap": _raw("raw_ap_journal_lines"),
    "ar": _raw("raw_ar_journal_lines"),
}

mappings = Table(
    "dataset_mappings",
    dataset_metadata,
    Column("id", Uuid, primary_key=True),
    Column("import_batch_id", Uuid, ForeignKey(batches.c.id), nullable=False),
    Column("mapping_version", String(64), nullable=False),
    Column("mapping_json", JSON, nullable=False),
    Column("mapping_hash", CHAR(64), nullable=False),
    Column("status", String(16), nullable=False),
    UniqueConstraint("import_batch_id", "mapping_version"),
    **MYSQL_OPTIONS,
)
counterparties = Table(
    "counterparties",
    dataset_metadata,
    Column("id", Uuid, primary_key=True),
    Column("import_batch_id", Uuid, ForeignKey(batches.c.id), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("account", String(64), nullable=False),
    Column("display_name", String(255), nullable=False),
    Column("currency", CURRENCY, nullable=False),
    Column("source_record_ref", Uuid, nullable=False),
    Column("source_system", String(40), nullable=False),
    Column("field_lineage_json", JSON, nullable=False),
    UniqueConstraint("import_batch_id", "kind", "account"),
    **MYSQL_OPTIONS,
)
journals = Table(
    "accounting_journal_lines",
    dataset_metadata,
    Column("id", Uuid, primary_key=True),
    Column("import_batch_id", Uuid, ForeignKey(batches.c.id), nullable=False),
    Column("counterparty_id", Uuid, ForeignKey(counterparties.c.id), nullable=False),
    Column("kind", String(2), nullable=False),
    Column("company", String(32), nullable=False),
    Column("offset_company", String(32)),
    Column("journal_date", Date, nullable=False),
    Column("line_number", Integer, nullable=False),
    Column("currency", CURRENCY, nullable=False),
    Column("debit", ExactMoney()),
    Column("credit", ExactMoney()),
    Column("source_record_ref", Uuid, nullable=False),
    Column("source_system", String(40), nullable=False),
    Column("field_lineage_json", JSON, nullable=False),
    UniqueConstraint("import_batch_id", "source_record_ref"),
    Index("ix_journal_batch_kind", "import_batch_id", "kind", "id"),
    **MYSQL_OPTIONS,
)
quarantine = Table(
    "data_quality_quarantine",
    dataset_metadata,
    Column("id", Uuid, primary_key=True),
    Column("import_batch_id", Uuid, ForeignKey(batches.c.id), nullable=False),
    Column("source_record_ref", Uuid, nullable=False),
    Column("source_file", String(255), nullable=False),
    Column("source_sheet", String(128), nullable=False),
    Column("source_row_number", Integer, nullable=False),
    Column("code", String(64), nullable=False),
    Column("reason", String(255), nullable=False),
    UniqueConstraint("import_batch_id", "source_record_ref", "code"),
    **MYSQL_OPTIONS,
)
versions = Table(
    "dataset_versions",
    dataset_metadata,
    Column("id", Uuid, primary_key=True),
    Column("import_batch_id", Uuid, ForeignKey(batches.c.id)),
    Column("scenario", String(32), nullable=False),
    Column("scenario_id", String(64), nullable=False),
    Column("scenario_version", String(32), nullable=False),
    Column("scenario_seed", Integer, nullable=False),
    Column("config_hash", CHAR(64), nullable=False),
    Column("as_of_date", Date, nullable=False),
    Column("config_json", JSON, nullable=False),
    Column("stats_json", JSON, nullable=False),
    Column("created_at", TIME_UTC, nullable=False),
    **MYSQL_OPTIONS,
)
current_source = Table(
    "dataset_current_source",
    dataset_metadata,
    Column("name", String(24), primary_key=True),
    Column("import_batch_id", Uuid, ForeignKey(batches.c.id), nullable=False),
    **MYSQL_OPTIONS,
)
active_scenarios = Table(
    "dataset_active_scenarios",
    dataset_metadata,
    Column("scenario", String(32), primary_key=True),
    Column("version_id", Uuid, ForeignKey(versions.c.id), nullable=False),
    **MYSQL_OPTIONS,
)
scenario_documents = Table(
    "demo_scenario_documents",
    dataset_metadata,
    Column("id", Uuid, primary_key=True),
    Column("version_id", Uuid, ForeignKey(versions.c.id), nullable=False),
    Column("tenant_id", String(64), nullable=False),
    Column("entity_id", Uuid, ForeignKey(entities.c.id), nullable=False),
    Column("document_type", String(2), nullable=False),
    Column("source_kind", String(40), nullable=False),
    Column("source_record_ref", Uuid),
    Column("canonical_record_id", Uuid, ForeignKey(journals.c.id)),
    Column("source_transform_note", String(255), nullable=False),
    Column("assumption_ids", JSON, nullable=False),
    Column("field_lineage_json", JSON, nullable=False),
    Column("open_balance_as_of", ExactMoney(), nullable=False),
    Column("external_document_no", String(64)),
    Column("bank_receipt_date", Date),
    Index("ix_scenario_scope", "version_id", "tenant_id", "entity_id", "document_type"),
    **MYSQL_OPTIONS,
)
source_links = Table(
    "source_row_links",
    dataset_metadata,
    Column("id", Uuid, primary_key=True),
    Column("import_batch_id", Uuid, ForeignKey(batches.c.id), nullable=False),
    Column("original_import_record_id", Uuid, nullable=False),
    Column("canonical_record_id", Uuid, nullable=False),
    Column("scenario_record_id", Uuid, ForeignKey(scenario_documents.c.id)),
    Column("source_file", String(255), nullable=False),
    Column("source_sheet", String(128), nullable=False),
    Column("source_row_number", Integer, nullable=False),
    Column("source_row_hash", CHAR(64), nullable=False),
    Index("ix_source_links_scenario", "scenario_record_id"),
    **MYSQL_OPTIONS,
)
run_source_links = Table(
    "run_source_links",
    dataset_metadata,
    Column("run_id", Uuid, ForeignKey(runs.c.run_id), primary_key=True),
    Column("scenario_record_id", Uuid, ForeignKey(scenario_documents.c.id), primary_key=True),
    **MYSQL_OPTIONS,
)
