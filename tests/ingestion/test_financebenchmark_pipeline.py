from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import func, select

from erp_agent.config import Settings
from erp_agent.database import dataset_tables as ds
from erp_agent.database.engine import database_engine
from erp_agent.database.repository import SQLAlchemyERPRepository
from erp_agent.ingestion.load_mysql import import_source
from erp_agent.ingestion.mapping import MAPPING_PATH, read_mapping
from erp_agent.ingestion.profile import profile
from erp_agent.ingestion.scenario_builder import build_scenarios, read_scenario, register_fixture
from erp_agent.ingestion.source_manifest import SOURCE_PATH, SourceError
from erp_agent.ingestion.source_reader import load_source
from scripts.seed_demo import seed
from scripts.verify_microsoft_import import verify


@pytest.fixture(scope="module")
def pinned_source():
    if not SOURCE_PATH.is_file():
        pytest.skip("Pinned Microsoft source is not available")
    identity, sheets = load_source(SOURCE_PATH)
    manifest, _ = profile(identity, sheets)
    return manifest, sheets


def test_atomic_header_mismatch_then_same_batch_recovery(
    demo_settings: Settings, pinned_source
) -> None:
    engine = database_engine(demo_settings.database_url)
    manifest, sheets = pinned_source
    mapping = read_mapping(MAPPING_PATH)
    malformed = [
        replace(s, headers=("unrecognized",)) if s.kind == "customers" else s for s in sheets
    ]
    with pytest.raises(SourceError, match="DATASET_HEADER_MISMATCH"):
        import_source(engine, manifest, malformed, mapping)
    with engine.connect() as conn:
        assert conn.scalar(select(ds.batches.c.status)) == "FAILED"
        assert conn.scalar(select(func.count()).select_from(ds.raw_tables["customers"])) == 0
        assert conn.scalar(select(ds.current_source.c.import_batch_id)) is None
    result = import_source(engine, manifest, sheets, mapping)
    replay = import_source(engine, manifest, sheets, mapping)
    assert result == replay
    verified = verify(engine)
    assert verified["row_hashes_verified"] == 8887
    assert sum(verified["canonical_counts"].values()) + verified["quarantined"] == 8887
    assert result["business_semantics"]["open_balance"]["status"] == "MISSING"


def test_orphan_counterparty_is_quarantined(demo_settings: Settings, pinned_source) -> None:
    engine = database_engine(demo_settings.database_url)
    manifest, sheets = pinned_source
    ap_sheet = next(s for s in sheets if s.kind == "ap" and s.rows)
    index = ap_sheet.headers.index("Offset Non-ledger account")
    row, *rest = ap_sheet.rows
    changed = list(row.values)
    changed[index] = "TEST-ORPHAN"
    table = replace(ap_sheet, rows=(replace(row, values=tuple(changed)), *rest))
    result = import_source(
        engine,
        {**manifest, "pinned_blob_verified": False},
        [table if s is ap_sheet else s for s in sheets],
        read_mapping(MAPPING_PATH),
    )
    assert result["quarantine_codes"]["ORPHAN_COUNTERPARTY"] == 1
    with pytest.raises(SourceError, match="MICROSOFT_IMPORT_NOT_VERIFIED"):
        build_scenarios(engine, read_scenario())


def test_scenario_versions_are_immutable_and_scoped(demo_settings: Settings, pinned_source) -> None:
    engine = database_engine(demo_settings.database_url)
    manifest, sheets = pinned_source
    import_source(engine, manifest, sheets, read_mapping(MAPPING_PATH))
    config = read_scenario()
    first = build_scenarios(engine, config)
    assert first == build_scenarios(engine, config)
    assert first["baseline"]["dated_main_documents_by_month"] == {
        "2026-12": 48,
        **{f"2027-{month:02}": 48 for month in range(1, 6)},
    }
    seed(engine, finance=True)
    fixture = register_fixture(engine)
    assert fixture == register_fixture(engine)
    reader = SQLAlchemyERPRepository(database_engine(demo_settings.read_url, read=True))
    as_of = date(2026, 11, 27)
    baseline = reader.snapshot("DEMO_A", ("TW01",), as_of, scenario="baseline")
    golden = reader.snapshot("DEMO_A", ("TW01",), as_of, ("USD",), "fixture_only")
    other = reader.snapshot("DEMO_B", ("TW01",), as_of, scenario="baseline")
    assert len(baseline.invoices) == 291 and len(other.invoices) == 1
    assert set(row.currency for row in baseline.invoices) == {"USD", "EUR", "JPY"}
    assert len(golden.invoices) == 5 and all(
        row.origin and not row.origin.source_record_ref for row in golden.invoices
    )
    assert (
        sum(
            row.origin is not None and row.origin.source_record_ref is not None
            for row in baseline.invoices
        )
        == 288
    )
    second = build_scenarios(
        engine, config.model_copy(update={"version": "2", "seed": config.seed + 1})
    )
    assert first["baseline"]["version_id"] != second["baseline"]["version_id"]
    with engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(ds.versions)) == 5
        assert conn.scalar(select(func.count()).select_from(ds.scenario_documents)) == 609
