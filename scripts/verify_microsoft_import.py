import json

from sqlalchemy import Engine, func, select

from erp_agent.config import Settings
from erp_agent.database import dataset_tables as ds
from erp_agent.database.engine import database_engine
from erp_agent.ingestion.source_manifest import SourceError
from erp_agent.ingestion.source_reader import content_hash


def verify(engine: Engine) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            select(ds.batches)
            .join(
                ds.current_source,
                ds.current_source.c.import_batch_id == ds.batches.c.id,
            )
            .where(ds.batches.c.status == "COMPLETED")
        ).first()
        if row is None:
            raise SourceError("SOURCE_DATA_UNAVAILABLE: completed import required")
        batch, manifest = row.id, row.manifest_json
        counts = {}
        for kind, table in ds.raw_tables.items():
            raw = conn.execute(
                select(table.c.raw_json, table.c.source_row_hash).where(
                    table.c.import_batch_id == batch
                )
            )
            counts[kind] = 0
            for raw_row in raw:
                counts[kind] += 1
                if content_hash(raw_row.raw_json) != raw_row.source_row_hash:
                    raise SourceError(f"DATASET_ROW_HASH_MISMATCH: {kind}")
        if counts != manifest["row_counts"]:
            raise SourceError("DATASET_ROW_COUNT_MISMATCH")
        for table, expected in (
            (
                ds.counterparties,
                manifest["canonical_counts"]["customers"] + manifest["canonical_counts"]["vendors"],
            ),
            (ds.journals, manifest["canonical_counts"]["ar"] + manifest["canonical_counts"]["ap"]),
            (ds.quarantine, manifest["quarantined"]),
        ):
            actual = conn.scalar(
                select(func.count()).select_from(table).where(table.c.import_batch_id == batch)
            )
            if actual != expected:
                raise SourceError(f"DATASET_COVERAGE_MISMATCH: {table.name}")
        raw_links = conn.scalar(
            select(func.count())
            .select_from(ds.source_links)
            .where(
                ds.source_links.c.import_batch_id == batch,
                ds.source_links.c.scenario_record_id.is_(None),
            )
        )
        if raw_links != manifest["canonical_mapped"]:
            raise SourceError("DATASET_LINEAGE_MISMATCH")
    return {
        "source_rows": counts,
        "import_batch_id": str(batch),
        "sha256": manifest["file_sha256"],
        "canonical_counts": manifest["canonical_counts"],
        "quarantined": manifest["quarantined"],
        "row_hashes_verified": sum(counts.values()),
        "canonical_links": raw_links,
    }


if __name__ == "__main__":
    print(json.dumps(verify(database_engine(Settings().import_url))))
