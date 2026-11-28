import asyncio
from pathlib import Path
from shutil import copyfile

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from erp_agent.api.main import create_app
from erp_agent.config import Settings
from erp_agent.database.engine import database_engine
from erp_agent.ingestion.load_mysql import import_source
from erp_agent.ingestion.mapping import MAPPING_PATH, read_mapping
from erp_agent.ingestion.profile import profile
from erp_agent.ingestion.scenario_builder import build_scenarios, read_scenario, register_fixture
from erp_agent.ingestion.source_manifest import SOURCE_PATH
from erp_agent.ingestion.source_reader import load_source
from erp_agent.mcp.server import create_mcp_app
from scripts.seed_demo import seed


@pytest.fixture
def demo_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    url = f"sqlite:///{(tmp_path / 'erp.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ERP_ADMIN_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    seed(database_engine(url))
    return Settings(
        _env_file=None,
        database_url=url,
        erp_read_database_url=url,
        session_database=str(tmp_path / "session.db"),
        mcp_internal_token="test-service",
        demo_api_keys_json=(
            '{"test-a":{"tenant_id":"demo-a","actor_id":"a","entity_ids":["TW01"]},'
            '"test-b":{"tenant_id":"demo-b","actor_id":"b","entity_ids":["TW01"]},'
            '"test-a2":{"tenant_id":"demo-a","actor_id":"a2","entity_ids":["TW02"]},'
            '"test-service-a":{"tenant_id":"demo-a","actor_id":"orchestrator",'
            '"actor_type":"service","entity_ids":["TW01","TW02"]}}'
        ),
    )


@pytest.fixture
def client(demo_settings: Settings):
    with TestClient(create_app(demo_settings)) as http:
        yield http


@pytest.fixture
async def mcp_http(demo_settings: Settings):
    host = create_mcp_app(demo_settings)
    started, stop = asyncio.Event(), asyncio.Event()

    async def lifespan() -> None:
        async with host.router.lifespan_context(host):
            started.set()
            await stop.wait()

    task = asyncio.create_task(lifespan())
    await asyncio.wait_for(started.wait(), timeout=5)
    try:
        yield httpx2.ASGITransport(app=host)
    finally:
        stop.set()
        await task


@pytest.fixture
def mcp_factory(mcp_http):
    def factory(**kwargs):
        return httpx2.AsyncClient(transport=mcp_http, **kwargs)

    return factory


@pytest.fixture(scope="session")
def finance_db_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not SOURCE_PATH.is_file():
        pytest.skip("Pinned Microsoft source is not available")
    path = tmp_path_factory.mktemp("financebenchmark") / "template.db"
    url = f"sqlite:///{path.as_posix()}"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", url)
        mp.setenv("ERP_ADMIN_DATABASE_URL", url)
        command.upgrade(Config("alembic.ini"), "head")
    engine = database_engine(url)
    identity, sheets = load_source(SOURCE_PATH)
    manifest, _ = profile(identity, sheets)
    import_source(engine, manifest, sheets, read_mapping(MAPPING_PATH))
    build_scenarios(engine, read_scenario())
    seed(engine, finance=True)
    register_fixture(engine)
    engine.dispose()
    return path


@pytest.fixture
def finance_settings(finance_db_template: Path, tmp_path: Path) -> Settings:
    path = tmp_path / "finance.db"
    copyfile(finance_db_template, path)
    url = f"sqlite:///{path.as_posix()}"
    return Settings(
        _env_file=None,
        database_url=url,
        erp_read_database_url=url,
        mcp_internal_token="finance-service",
        session_database=str(tmp_path / "finance-sessions.db"),
        demo_api_keys_json=(
            '{"admin":{"tenant_id":"DEMO_A","actor_id":"admin","entity_ids":["TW01"],"dataset_admin":true},'
            '"analyst":{"tenant_id":"DEMO_A","actor_id":"analyst","entity_ids":["TW01"]},'
            '"own-other":{"tenant_id":"DEMO_A","actor_id":"tw02","entity_ids":["TW02"]},'
            '"other":{"tenant_id":"DEMO_B","actor_id":"other","entity_ids":["TW01"],"dataset_admin":true}}'
        ),
    )
