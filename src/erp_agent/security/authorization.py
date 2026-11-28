from hmac import compare_digest
from typing import Literal

from pydantic import Field, TypeAdapter

from erp_agent.contracts.errors import ServiceError
from erp_agent.contracts.requests import EntityCode, InputModel


class Principal(InputModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(min_length=1, max_length=128)
    actor_type: Literal["user", "service"] = "user"
    dataset_admin: bool = False
    entity_ids: frozenset[EntityCode] = Field(min_length=1, max_length=20)

    def scope(self, requested: list[str] | None) -> tuple[str, ...]:
        selected = set(requested) if requested is not None else self.entity_ids
        if not selected <= self.entity_ids:
            raise ServiceError(
                "ENTITY_FORBIDDEN", "One or more entities are outside your scope", 403
            )
        return tuple(sorted(selected))


class DemoAuth:
    def __init__(self, mapping: str) -> None:
        self._mapping = TypeAdapter(dict[str, Principal]).validate_json(mapping)
        if any(not key.strip() or len(key) > 256 or not key.isascii() for key in self._mapping):
            raise ValueError("Demo API keys must be nonempty ASCII strings of up to 256 characters")

    def authenticate(self, key: str | None) -> Principal:
        if key and len(key) <= 256:
            for candidate, principal in self._mapping.items():
                if compare_digest(candidate.encode(), key.encode()):
                    return principal
        raise ServiceError("UNAUTHENTICATED", "A valid API key is required", 401)
