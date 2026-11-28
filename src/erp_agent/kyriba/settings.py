from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from erp_agent.contracts.requests import EntityCode, InputModel
from erp_agent.kyriba.models import ProviderCode


class EntityMapping(InputModel):
    company_code: ProviderCode
    account_codes: frozenset[ProviderCode] = Field(default_factory=frozenset, max_length=100)


class KyribaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KYRIBA_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
        env_ignore_empty=True,
    )

    mode: Literal["disabled", "mock", "live"] = "disabled"
    token_url: HttpUrl | None = None
    base_url: HttpUrl | None = None
    client_id: SecretStr | None = None
    client_secret: SecretStr | None = None
    entity_map: dict[str, dict[EntityCode, EntityMapping]] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    retry_wait_seconds: float = Field(default=5.0, ge=0, le=30)
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=10, ge=1, le=10)

    @model_validator(mode="after")
    def validate_live(self) -> Self:
        if self.mode != "live":
            return self
        if not self.token_url or not self.base_url or not self.client_id or not self.client_secret:
            raise ValueError("Live Kyriba requires token/base URLs and client credentials")
        if (
            not self.client_id.get_secret_value().strip()
            or not self.client_secret.get_secret_value().strip()
        ):
            raise ValueError("Live Kyriba requires nonempty client credentials")
        if ":" in self.client_id.get_secret_value():
            raise ValueError("Kyriba client ID cannot contain the Basic auth separator")
        for url in (self.token_url, self.base_url):
            if url.scheme != "https" or any(
                part is not None for part in (url.username, url.password, url.query, url.fragment)
            ):
                raise ValueError(
                    "Kyriba URLs must be HTTPS without credentials, queries or fragments"
                )
        if self.token_url.path != "/oauth/token":
            raise ValueError("KYRIBA_TOKEN_URL must reference the OAuth token endpoint")
        if not any(self.entity_map.values()):
            raise ValueError("Live Kyriba requires KYRIBA_ENTITY_MAP")
        companies: set[str] = set()
        accounts: set[str] = set()
        for entities in self.entity_map.values():
            for mapping in entities.values():
                company = mapping.company_code.upper()
                codes = {code.upper() for code in mapping.account_codes}
                if (
                    company in companies
                    or codes & accounts
                    or len(codes) != len(mapping.account_codes)
                ):
                    raise ValueError(
                        "Kyriba company and account codes must map to exactly one ERP entity"
                    )
                companies.add(company)
                accounts.update(codes)
        return self
