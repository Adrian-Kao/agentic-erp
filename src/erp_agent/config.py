from datetime import date
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///erp-demo.db"
    erp_read_database_url: str | None = None
    erp_admin_database_url: str | None = None
    erp_import_database_url: str | None = None
    dataset_repo: str = "microsoft/FinanceBenchmark"
    dataset_commit: str = "6ced62b961d4c18b2ba53f268b443eb852fb73ca"
    dataset_zip_path: Path = Path("data/source/FO Benchmark_Data.zip")
    demo_scenario: str = "aster-treasury-v1"
    demo_scenario_seed: int = 113306018
    demo_as_of_date: date = date(2026, 11, 27)
    demo_api_keys_json: SecretStr = SecretStr("{}")
    mcp_server_url: str = "http://127.0.0.1:8001/mcp"
    mcp_internal_token: SecretStr = SecretStr("")
    mcp_allowed_hosts: str = "localhost,127.0.0.1,mcp"
    enable_llm_agent: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = ""
    log_level: str = "INFO"
    session_database: str = "erp-sessions.db"

    @property
    def read_url(self) -> str:
        if self.erp_read_database_url:
            writer, reader = map(make_url, (self.database_url, self.erp_read_database_url))
            if writer.get_backend_name() != "sqlite" and (
                reader.get_backend_name() != writer.get_backend_name()
                or writer.username == reader.username
                or writer.database != reader.database
            ):
                raise ValueError(
                    "Report and ERP access require distinct roles in the same database"
                )
            return self.erp_read_database_url
        if self.database_url.startswith("sqlite:"):
            return self.database_url
        raise ValueError("ERP_READ_DATABASE_URL is required for server databases")

    @property
    def import_url(self) -> str:
        if self.erp_import_database_url:
            return self.erp_import_database_url
        if self.database_url.startswith("sqlite:"):
            return self.database_url
        raise ValueError("ERP_IMPORT_DATABASE_URL is required for dataset writes")
