from fastapi.testclient import TestClient
from sqlalchemy import func, select

from erp_agent.api.main import create_app
from erp_agent.config import Settings
from erp_agent.database.engine import database_engine
from erp_agent.database.tables import audit, items, runs

PATH = "/v1/exposures/analyze"
BODY = {
    "entity_ids": ["TW01"],
    "as_of_date": "2026-11-27",
    "horizon_months": 6,
    "currencies": ["USD"],
    "include_overdue_summary": True,
}
AUTH = {"X-API-Key": "test-a"}


def test_full_report_and_idempotency(client: TestClient, demo_settings: Settings) -> None:
    headers = {**AUTH, "Idempotency-Key": "demo-1"}
    response = client.post(PATH, json=BODY, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["X-Request-ID"]
    report = response.json()
    assert report["status"] == "COMPLETED"
    assert report["exposures"][0]["period"] == "2027-01"
    assert report["exposures"][0]["net_exposure"] == "400000.0000"
    assert report["overdue_summary"][0]["overdue_ar"] == "25000.0000"
    assert client.get(f"/v1/exposures/{report['run_id']}", headers=AUTH).json() == report
    assert client.post(PATH, json=BODY, headers=headers).json() == report
    conflict = client.post(PATH, json={**BODY, "horizon_months": 3}, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    with database_engine(demo_settings.database_url).connect() as conn:
        assert conn.scalar(select(func.count()).select_from(runs)) == 1
        assert conn.scalar(select(func.count()).select_from(items)) == 1
        assert conn.scalar(select(func.count()).select_from(audit)) == 3


def test_tenant_and_entity_isolation(client: TestClient) -> None:
    assert client.post(PATH, json=BODY).status_code == 401
    assert client.get("/v1/erp/schema").status_code == 401
    assert client.post(PATH, json={**BODY, "entity_ids": ["TW02"]}, headers=AUTH).status_code == 403
    assert client.post(PATH, json={**BODY, "tenant_id": "demo-b"}, headers=AUTH).status_code == 422
    assert client.post(PATH, json={**BODY, "currencies": ["FAK"]}, headers=AUTH).status_code == 422
    assert client.post(PATH, content="x" * 40_000, headers=AUTH).status_code == 413
    run_id = client.post(PATH, json=BODY, headers=AUTH).json()["run_id"]
    for key in ("test-b", "test-a2"):
        assert client.get(f"/v1/exposures/{run_id}", headers={"X-API-Key": key}).status_code == 404
    assert client.get("/v1/erp/entities", headers=AUTH).json()[0]["entity_id"] == "TW01"


def test_partial_and_mas_envelope(client: TestClient) -> None:
    body = {**BODY, "currencies": None}
    partial = client.post(PATH, json=body, headers=AUTH).json()
    assert partial["status"] == "PARTIAL"
    assert partial["data_quality_issues"][0]["code"] == "MISSING_DUE_DATE"
    request = {
        "task_id": "TASK-001",
        "source_agent": "orchestrator",
        "target_agent": "erp_agent",
        "action": "analyze_fx_exposure",
        "parameters": BODY,
    }
    assert client.post("/v1/mas/tasks", json=request, headers=AUTH).status_code == 403
    response = client.post("/v1/mas/tasks", json=request, headers={"X-API-Key": "test-service-a"})
    assert response.status_code == 200
    assert response.json()["result"]["run_id"] == response.json()["run_id"]


def test_health_openapi_and_database_unavailable(demo_settings: Settings) -> None:
    broken = demo_settings.model_copy(update={"database_url": "sqlite:///:memory:"})
    with TestClient(create_app(broken)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/docs").status_code == 200
        assert "ExposureReport" in client.get("/openapi.json").json()["components"]["schemas"]
        assert client.get("/readyz").status_code == 503
        response = client.post(PATH, json=BODY, headers=AUTH)
        assert response.status_code == 503
        assert response.json()["code"] == "DATABASE_UNAVAILABLE"
