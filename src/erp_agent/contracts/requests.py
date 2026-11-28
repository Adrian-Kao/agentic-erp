from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

# Supported subset of ISO 4217. Extend only with a reviewed ERP currency mapping.
CURRENCIES = frozenset("AUD CAD CHF CNY EUR GBP HKD JPY KRW MYR NZD SGD THB TWD USD VND".split())
EntityCode = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,31}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
ScenarioName = Literal["baseline", "data_quality_faults", "fixture_only"]


def _currencies(values: list[str]) -> list[str]:
    if not set(values) <= CURRENCIES:
        raise ValueError("Unsupported currency")
    return sorted(set(values))


Currencies = Annotated[
    list[Currency], Field(min_length=1, max_length=16), AfterValidator(_currencies)
]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ExposureRequest(InputModel):
    entity_ids: list[EntityCode] = Field(min_length=1, max_length=20)
    as_of_date: date = Field(ge=date(1900, 1, 1), le=date(9998, 12, 31))
    horizon_months: int = Field(default=6, ge=3, le=6, strict=True)
    currencies: Currencies | None = None
    include_overdue_summary: bool = True
    scenario: ScenarioName | None = None

    @field_validator("entity_ids")
    @classmethod
    def normalize_entities(cls, values: list[str]) -> list[str]:
        return sorted(set(values))


class QueryFilters(InputModel):
    entity_ids: list[EntityCode] | None = Field(default=None, min_length=1, max_length=20)
    as_of_date: date | None = Field(default=None, ge=date(1900, 1, 1), le=date(9998, 12, 31))
    currencies: Currencies | None = None
    scenario: ScenarioName | None = None
