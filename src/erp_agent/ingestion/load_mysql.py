import json
from collections import Counter
from datetime import UTC, datetime
from itertools import islice
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import Connection, Engine, Table, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from erp_agent.database.dataset_tables import (
    batches,
    counterparties,
    current_source,
    journals,
    mappings,
    quarantine,
    raw_tables,
    source_links,
)
from erp_agent.ingestion.mapping import (
    RowError,
    candidate_master_counts,
    journal_values,
    mapped_sheets,
    master_values,
    row_values,
    source_id,
)
from erp_agent.ingestion.source_manifest import SourceError
from erp_agent.ingestion.source_reader import Sheet, canonical_json, content_hash


def bulk_insert(conn: Connection, table: Table, rows: list[dict[str, Any]]) -> None:
    pending = iter(rows)
    while chunk := list(islice(pending, 400)):
        conn.execute(insert(table), chunk)


def set_pointer(conn: Connection, table: Table, key: str, name: str, **values: Any) -> None:
    selector = table.c[key] == name
    present = conn.scalar(select(table.c[key]).where(selector).with_for_update())
    if present is None:
        conn.execute(insert(table).values({key: name, **values}))
    else:
        conn.execute(update(table).where(selector).values(**values))


def _load_rows(
    conn: Connection,
    batch: UUID,
    manifest: dict[str, Any],
    sheets: list[Sheet],
    mapping: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    selected = mapped_sheets(sheets, mapping)
    masters: dict[tuple[str, str], UUID] = {}
    counts: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    links: list[dict[str, Any]] = []
    rejects: list[dict[str, Any]] = []
    source_system = (
        "MICROSOFT_FINANCEBENCHMARK" if manifest["pinned_blob_verified"] else "TEST_FIXTURE"
    )
    for kind in ("customers", "vendors", "ap", "ar"):
        sheet, indexes = selected[kind]
        mapped_fields = mapping["tables"][kind]["fields"]
        is_master = kind in {"customers", "vendors"}
        account_counts = (
            candidate_master_counts(sheet, indexes["account"]) if is_master else Counter()
        )
        raw_rows: list[dict[str, Any]] = []
        canonical_rows: list[dict[str, Any]] = []
        for row in sheet.rows:
            raw_id = source_id(batch, sheet, row)
            raw = row.raw(sheet.headers)
            locator = {
                "source_file": sheet.file,
                "source_sheet": sheet.name,
                "source_row_number": row.number,
            }
            digest = content_hash(raw)
            raw_rows.append(
                {
                    "id": raw_id,
                    "import_batch_id": batch,
                    **locator,
                    "source_row_hash": digest,
                    "raw_json": json.loads(canonical_json(raw)),
                    "imported_at": now,
                }
            )
            try:
                values = row_values(row, indexes)
                if is_master:
                    canonical = master_values(values)
                    account = canonical["account"]
                    if account_counts[account] != 1:
                        raise RowError(
                            "DUPLICATE_COUNTERPARTY", "Source account is not unique in its master"
                        )
                else:
                    canonical = journal_values(values, kind, masters)
            except RowError as exc:
                errors[exc.code] += 1
                rejects.append(
                    {
                        "id": uuid5(raw_id, exc.code),
                        "import_batch_id": batch,
                        "source_record_ref": raw_id,
                        **locator,
                        "code": exc.code,
                        "reason": str(exc),
                    }
                )
                continue
            canonical_id = uuid5(raw_id, mapping["version"])
            if is_master:
                masters[kind, account] = canonical_id
                canonical["kind"] = "CUSTOMER" if kind == "customers" else "VENDOR"
            canonical_rows.append(
                {
                    "id": canonical_id,
                    "import_batch_id": batch,
                    "source_record_ref": raw_id,
                    "source_system": source_system,
                    "field_lineage_json": mapped_fields,
                    **canonical,
                }
            )
            links.append(
                {
                    "id": uuid5(raw_id, "canonical-link"),
                    "import_batch_id": batch,
                    "original_import_record_id": raw_id,
                    "canonical_record_id": canonical_id,
                    "source_row_hash": digest,
                    **locator,
                }
            )
        bulk_insert(conn, raw_tables[kind], raw_rows)
        bulk_insert(conn, counterparties if is_master else journals, canonical_rows)
        raw_count = conn.scalar(
            select(func.count())
            .select_from(raw_tables[kind])
            .where(raw_tables[kind].c.import_batch_id == batch)
        )
        if raw_count != manifest["row_counts"][kind]:
            raise SourceError(f"DATASET_ROW_COUNT_MISMATCH: {kind}")
        counts[kind] = len(canonical_rows)
    bulk_insert(conn, quarantine, rejects)
    bulk_insert(conn, source_links, links)
    if sum(counts.values()) + len(rejects) != manifest["source_rows_read"]:
        raise SourceError("DATASET_COVERAGE_MISMATCH")
    return {
        **manifest,
        "mapping_version": mapping["version"],
        "mapping_hash": content_hash(mapping),
        "canonical_counts": dict(counts),
        "canonical_mapped": sum(counts.values()),
        "quarantined": len(rejects),
        "quarantine_codes": dict(errors),
        "business_semantics": mapping["semantics"],
    }


def import_source(
    engine: Engine, manifest: dict[str, Any], sheets: list[Sheet], mapping: dict[str, Any]
) -> dict[str, Any]:
    batch = UUID(manifest["import_batch_id"])
    now = datetime.now(UTC)
    mapping_hash = content_hash(mapping)
    with engine.connect() as conn:
        existing = conn.scalar(select(batches.c.id).where(batches.c.id == batch))
    if existing is None:
        try:
            with engine.begin() as conn:
                conn.execute(
                    insert(batches).values(
                        id=batch,
                        upstream_repo=manifest["upstream_repo"],
                        upstream_commit=manifest["upstream_commit"],
                        source_sha256=manifest["file_sha256"],
                        mapping_hash=mapping_hash,
                        status="PENDING",
                        manifest_json=manifest,
                        source_rows=manifest["source_rows_read"],
                        canonical_rows=0,
                        quarantined_rows=0,
                        created_at=now,
                    )
                )
        except IntegrityError:
            # A peer may have created this exact source batch concurrently.
            with engine.connect() as conn:
                if conn.scalar(select(batches.c.id).where(batches.c.id == batch)) is None:
                    raise
    try:
        with engine.begin() as conn:
            row = conn.execute(select(batches).where(batches.c.id == batch).with_for_update()).one()
            if row.mapping_hash != mapping_hash:
                raise SourceError(
                    "DATASET_MAPPING_CONFLICT: this pinned batch already has a different mapping"
                )
            if row.status == "COMPLETED":
                return dict(row.manifest_json)
            conn.execute(
                update(batches)
                .where(batches.c.id == batch)
                .values(status="RUNNING", error_code=None)
            )
            result = _load_rows(conn, batch, manifest, sheets, mapping, now)
            conn.execute(
                insert(mappings).values(
                    id=uuid5(batch, mapping["version"]),
                    import_batch_id=batch,
                    mapping_version=mapping["version"],
                    mapping_json=mapping,
                    mapping_hash=mapping_hash,
                    status="APPLIED",
                )
            )
            conn.execute(
                update(batches)
                .where(batches.c.id == batch)
                .values(
                    status="COMPLETED",
                    manifest_json=result,
                    canonical_rows=result["canonical_mapped"],
                    quarantined_rows=result["quarantined"],
                    completed_at=datetime.now(UTC),
                )
            )
            set_pointer(conn, current_source, "name", "financebenchmark", import_batch_id=batch)
            return result
    except Exception as exc:
        code = (
            str(exc).partition(":")[0] if isinstance(exc, SourceError) else "DATASET_IMPORT_FAILED"
        )
        with engine.begin() as conn:
            conn.execute(
                update(batches)
                .where(
                    batches.c.id == batch,
                    batches.c.status != "COMPLETED",
                    batches.c.mapping_hash == mapping_hash,
                )
                .values(status="FAILED", error_code=code[:64], completed_at=datetime.now(UTC))
            )
        raise
