from fastapi.testclient import TestClient

from erp_agent.api.main import create_app
from erp_agent.config import Settings
from erp_agent.database.engine import database_engine
from erp_agent.ingestion.scenario_builder import build_scenarios, read_scenario

BASE = {"entity_ids": ["TW01"], "as_of_date": "2026-11-27", "currencies": ["USD"]}
ADMIN = {"X-API-Key": "admin"}
ANALYST = {"X-API-Key": "analyst"}
ANALYZE = "/v1/exposures/analyze"


def test_financebenchmark_manifest_scope_and_persisted_lineage(finance_settings: Settings) -> None:
    with TestClient(create_app(finance_settings)) as api:
        assert api.get("/v1/datasets/manifest").status_code == 401
        assert api.get("/v1/datasets/manifest", headers=ANALYST).status_code == 403
        manifest = api.get("/v1/datasets/manifest", headers=ADMIN).json()
        assert manifest["source_rows_read"] == 8887 and manifest["pinned_blob_verified"]
        quality = api.get("/v1/datasets/quality", headers=ANALYST).json()
        assert {"due_date", "open_balance", "settlement_reference"} <= set(quality["warnings"])
        scenarios = api.get("/v1/scenarios", headers=ANALYST).json()
        assert {row["scenario"] for row in scenarios} == {
            "baseline",
            "fixture_only",
            "data_quality_faults",
        }
        cross = api.get("/v1/scenarios", headers={"X-API-Key": "other"}).json()
        assert "data_quality_faults" not in {row["scenario"] for row in cross}
        headers = {**ADMIN, "Idempotency-Key": "financebaseline"}
        params = {**BASE, "scenario": "baseline"}
        response = api.post(ANALYZE, json=params, headers=headers)
        assert response.status_code == 200, response.text
        report = response.json()
        assert (
            report["status"] == "COMPLETED" and report["provenance"]["scenario_rows_derived"] == 96
        )
        assert report["provenance"]["import_batch_id"] == manifest["import_batch_id"]
        assert (
            next(r["net_exposure"] for r in report["exposures"] if r["period"] == "2027-01")
            == "-957.4150"
        )
        path = f"/v1/exposures/{report['run_id']}/lineage"
        assert api.get(path, headers={"X-API-Key": "other"}).status_code == 404
        assert api.get(path, headers={"X-API-Key": "own-other"}).status_code == 404
        assert api.get(f"{path}?include_records=true", headers=ANALYST).status_code == 403
        lineage = api.get(f"{path}?include_records=true&limit=100", headers=ADMIN).json()
        assert lineage["linked_record_count"] == 97 and len(lineage["records"]) == 97
        source = next(row for row in lineage["records"] if row["raw_record_id"])
        assert source["source_sheet"] == "Sheet1" and source["source_row_number"] > 10
        assert len(source["source_row_hash"]) == 64 and "raw_json" not in source
        assert (
            api.post(
                ANALYZE, json={**params, "scenario": "fixture_only"}, headers=headers
            ).status_code
            == 409
        )
        scenario = read_scenario()
        build_scenarios(
            database_engine(finance_settings.database_url),
            scenario.model_copy(update={"version": "2", "seed": scenario.seed + 5}),
        )
        assert api.post(ANALYZE, json=params, headers=headers).json() == report
        assert api.get(f"{path}?include_records=true&limit=100", headers=ADMIN).json() == lineage


def test_financebenchmark_golden_faults_and_horizon(finance_settings: Settings) -> None:
    with TestClient(create_app(finance_settings)) as api:
        golden = api.post(
            ANALYZE, json={**BASE, "scenario": "fixture_only"}, headers=ANALYST
        ).json()
        assert golden["provenance"]["import_batch_id"] is None
        assert golden["provenance"]["source_system"] == "DEMO_SYNTHETIC_FIXTURE"
        assert golden["exposures"][0]["net_exposure"] == "400000.0000"
        faults = api.post(
            ANALYZE, json={**BASE, "scenario": "data_quality_faults"}, headers=ANALYST
        ).json()
        assert faults["status"] == "FAILED" and not faults["exposures"]
        assert {
            "MISSING_CURRENCY",
            "MISSING_DUE_DATE",
            "OVERPAYMENT",
            "DUPLICATE_EXTERNAL_DOCUMENT",
        } <= {row["code"] for row in faults["data_quality_issues"]}
        future = api.post(
            ANALYZE,
            json={
                **BASE,
                "scenario": "baseline",
                "as_of_date": "2026-12-01",
                "currencies": ["USD", "EUR", "JPY"],
            },
            headers=ANALYST,
        ).json()
        assert len(future["exposures"]) == 18
        assert max(group["period"] for group in future["exposures"]) == "2027-05"
