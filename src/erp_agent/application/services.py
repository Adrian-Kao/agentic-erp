from sqlalchemy import select

from erp_agent.application.dataset_service import DatasetService
from erp_agent.application.exposure_service import ExposureService
from erp_agent.application.query_service import QueryService
from erp_agent.config import Settings
from erp_agent.database.engine import database_engine
from erp_agent.database.repository import SQLAlchemyERPRepository, dataset_snapshot
from erp_agent.database.tables import entities, runs
from erp_agent.memory.run_store import RunStore
from erp_agent.security.authorization import DemoAuth


class Services:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.auth = DemoAuth(settings.demo_api_keys_json.get_secret_value())
        self.writer = database_engine(settings.database_url)
        self.reader = database_engine(settings.read_url, read=True)
        repository = SQLAlchemyERPRepository(self.reader)
        self.queries = QueryService(repository)
        self.exposures = ExposureService(repository, RunStore(self.writer))
        self.datasets = DatasetService(self.reader, self.writer)

    def ready(self) -> None:
        with self.writer.connect() as writer, self.reader.connect() as reader:
            writer.execute(select(runs.c.run_id).limit(1))
            reader.execute(select(entities.c.id).limit(1))
            if self.reader.dialect.name == "mysql":
                dataset_snapshot(reader, "baseline")

    def close(self) -> None:
        self.reader.dispose()
        self.writer.dispose()
