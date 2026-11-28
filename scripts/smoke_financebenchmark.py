"""Smoke-test the imported baseline, isolated golden fixture and MySQL permissions."""

import argparse
import asyncio
import json
from uuid import uuid4

import httpx
from sqlalchemy import false, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from erp_agent.config import Settings
from erp_agent.database.dataset_tables import raw_tables, run_source_links
from erp_agent.database.engine import database_engine
from erp_agent.database.tables import ar, audit
from erp_agent.mcp.client import connect_mcp
from scripts.smoke_test import REQUEST, find_key, verify_api, verify_mcp

BASELINE = {**REQUEST, "scenario": "baseline"}
GOLDEN = {**REQUEST, "scenario": "fixture_only"}


def get_json(response: httpx.Response) -> dict:
    response.raise_for_status()
    return response.json()


def verify_source(base_url: str, key: str, other_key: str) -> dict:
    with httpx.Client(base_url=base_url, headers={"X-API-Key": key}, timeout=15) as client:
        manifest = get_json(client.get("/v1/datasets/manifest"))
        assert manifest["pinned_blob_verified"] and manifest["source_rows_read"] == 8887
        assert manifest["row_counts"] == {
            "customers": 1000,
            "vendors": 1000,
            "ap": 3428,
            "ar": 3459,
        }
        quality = get_json(client.get("/v1/datasets/quality"))
        assert {"due_date", "settlement_reference", "open_balance"} <= set(quality["warnings"])
        assert {row["scenario"] for row in get_json(client.get("/v1/scenarios"))} == {
            "baseline",
            "data_quality_faults",
            "fixture_only",
        }
        report = get_json(
            client.post(
                "/v1/exposures/analyze",
                json=BASELINE,
                headers={"Idempotency-Key": f"baseline-{uuid4()}"},
            )
        )
        provenance = report["provenance"]
        assert report["status"] == "COMPLETED" and provenance["scenario_rows_derived"] == 96
        assert provenance["import_batch_id"] == manifest["import_batch_id"]
        jan = next(group for group in report["exposures"] if group["period"] == "2027-01")
        path = f"/v1/exposures/{report['run_id']}/lineage"
        lineage = get_json(client.get(path, params={"include_records": "true", "limit": 100}))
        assert lineage["linked_record_count"] == len(lineage["records"]) == 97
        assert sum(item["raw_record_id"] is not None for item in lineage["records"]) == 96
        assert client.get(path, headers={"X-API-Key": other_key}).status_code == 404
        faults = get_json(
            client.post(
                "/v1/exposures/analyze", json={**BASELINE, "scenario": "data_quality_faults"}
            )
        )
        assert faults["status"] == "FAILED" and not faults["exposures"]
        assert {"MISSING_CURRENCY", "OVERPAYMENT", "DUPLICATE_EXTERNAL_DOCUMENT"} <= {
            entry["code"] for entry in faults["data_quality_issues"]
        }
        assert (
            client.post(
                "/v1/agent/run", json={"session_id": "finance-smoke", "message": "analysis"}
            ).json()["code"]
            == "AGENT_UNAVAILABLE"
        )
        print(
            json.dumps(
                {
                    "interface": "FinanceBenchmark REST",
                    "version": manifest["upstream_commit"],
                    "run_id": report["run_id"],
                    "import_batch_id": manifest["import_batch_id"],
                    "raw_counts": manifest["row_counts"],
                    "canonical_counts": manifest["canonical_counts"],
                    "quarantined": manifest["quarantined"],
                    "baseline_usd_2027_01": jan,
                    "linked_documents": lineage["linked_record_count"],
                    "source_linked_documents": 96,
                    "fault_status": faults["status"],
                }
            )
        )
        return report


async def verify_stream(settings: Settings, key: str, report: dict) -> None:
    await verify_mcp(settings, key, report, request=BASELINE)
    async with connect_mcp(settings, key, str(uuid4())) as mcp:
        subset = await mcp.call_tool(
            "query_erp_sql",
            {
                "query_name": "source_lineage",
                "filters": {
                    "entity_ids": ["TW01"],
                    "as_of_date": "2026-11-27",
                    "scenario": "baseline",
                },
                "limit": 3,
            },
        )
        assert subset.structured_content and subset.structured_content["error"] is None
        assert len(subset.structured_content["result"]["rows"]) == 3
        rejection = await mcp.call_tool(
            "query_erp_sql", {"query_name": "raw_customers", "filters": {}}
        )
        assert (
            rejection.structured_content
            and rejection.structured_content["error"]["code"] == "QUERY_NOT_ALLOWED"
        )
        print("FinanceBenchmark MCP lineage allowed, raw tables rejected")


def _deny(conn: Connection, statement) -> None:
    try:
        conn.execute(statement)
    except DBAPIError as exc:
        assert exc.orig.args[0] in {1142, 1143, 1792}
        return
    raise AssertionError("Unexpected database permission")


def verify_mysql_roles(settings: Settings, run_id: str) -> None:
    reader, writer = (
        database_engine(settings.read_url, read=True),
        database_engine(settings.database_url),
    )
    try:
        with reader.connect() as read, writer.connect() as write:
            version = read.exec_driver_sql("SELECT VERSION()").scalar_one()
            assert version.startswith("8.4.")
            assert (
                read.exec_driver_sql("SELECT @@transaction_isolation").scalar_one()
                == "REPEATABLE-READ"
            )
            assert read.exec_driver_sql("SELECT @@transaction_read_only").scalar_one() == 1
            assert read.scalar(select(ar.c.id).limit(1))
            _deny(read, select(raw_tables["customers"].c.id).limit(1))
            _deny(write, select(ar.c.id).limit(1))
            _deny(write, update(ar).where(false()).values(status="VOID"))
            assert write.scalar(select(audit.c.id).where(audit.c.operation == "analyze").limit(1))
            assert write.scalar(
                select(run_source_links.c.scenario_record_id)
                .where(run_source_links.c.run_id == run_id)
                .limit(1)
            )
        print(f"MySQL {version}, reader snapshot, raw permissions and persisted audit verified")
    finally:
        reader.dispose()
        writer.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    settings = Settings()
    mapping = json.loads(settings.demo_api_keys_json.get_secret_value())
    admin_key = next(
        (
            key
            for key, p in mapping.items()
            if p["tenant_id"] == "DEMO_A" and "TW01" in p["entity_ids"] and p.get("dataset_admin")
        ),
        None,
    )
    if not admin_key:
        raise SystemExit("Configure a DEMO_A/TW01 key with dataset_admin=true")
    other = find_key(settings, "DEMO_B")
    golden = verify_api(args.api, admin_key, other, request=GOLDEN)
    assert golden["provenance"]["source_system"] == "DEMO_SYNTHETIC_FIXTURE"
    assert golden["provenance"]["import_batch_id"] is None
    report = verify_source(args.api, admin_key, other)
    asyncio.run(verify_stream(settings, admin_key, report))
    verify_mysql_roles(settings, report["run_id"])


if __name__ == "__main__":
    main()
