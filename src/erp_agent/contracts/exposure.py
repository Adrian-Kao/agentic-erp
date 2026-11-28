from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, PlainSerializer, WithJsonSchema, model_validator

from erp_agent.contracts.requests import Currency, EntityCode

ZERO = Decimal("0.0000")
Status = Literal["COMPLETED", "PARTIAL", "FAILED"]
ValidationStatus = Literal["PASSED", "WARNINGS", "FAILED"]
Direction = Literal["NET_INFLOW", "NET_OUTFLOW", "BALANCED"]
Money = Annotated[
    Decimal,
    Field(max_digits=20, decimal_places=4),
    PlainSerializer(lambda v: format(v, ".4f"), return_type=str, when_used="json"),
    WithJsonSchema(
        {"type": "string", "pattern": r"^-?[0-9]{1,16}\.[0-9]{4}$"}, mode="serialization"
    ),
]


def direction(net: Decimal) -> Direction:
    if net > ZERO:
        return "NET_INFLOW"
    return "NET_OUTFLOW" if net < ZERO else "BALANCED"


class ExposureItem(BaseModel):
    entity_id: EntityCode
    functional_currency: Currency
    currency: Currency
    period: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    ar_open: Money
    ap_open: Money
    net_exposure: Money
    direction: Direction
    source_ar_count: int = Field(ge=0)
    source_ap_count: int = Field(ge=0)

    @model_validator(mode="after")
    def check_totals(self) -> Self:
        if (
            self.ar_open < ZERO
            or self.ap_open < ZERO
            or self.currency == self.functional_currency
            or self.net_exposure != self.ar_open - self.ap_open
            or self.direction != direction(self.net_exposure)
        ):
            raise ValueError("Inconsistent exposure item")
        return self


class OverdueItem(BaseModel):
    entity_id: EntityCode
    currency: Currency
    overdue_ar: Annotated[Money, Field(ge=ZERO)]
    overdue_ap: Annotated[Money, Field(ge=ZERO)]
    record_count: int = Field(gt=0)


class QualityIssue(BaseModel):
    code: str
    severity: Literal["INFO", "WARNING", "ERROR"]
    message: str
    record_ref: str | None = None


class Provenance(BaseModel):
    source_system: str
    data_version: str
    input_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    source_entity_count: int = Field(ge=0)
    source_ar_count: int = Field(ge=0)
    source_ap_count: int = Field(ge=0)
    source_allocation_count: int = Field(ge=0)
    upstream_repo: str | None = None
    upstream_commit: str | None = None
    source_sha256: str | None = None
    import_batch_id: UUID | None = None
    dataset_version_id: UUID | None = None
    scenario: str | None = None
    scenario_id: str | None = None
    scenario_version: str | None = None
    scenario_seed: int | None = None
    scenario_config_hash: str | None = None
    source_rows_read: int = Field(default=0, ge=0)
    canonical_mapped: int = Field(default=0, ge=0)
    quarantined: int = Field(default=0, ge=0)
    scenario_rows_derived: int = Field(default=0, ge=0)
    synthetic_only_rows: int = Field(default=0, ge=0)
    source_reference_count: int = Field(default=0, ge=0)
    missing_required_fields: list[str] = Field(default_factory=list)
    participating_source_kinds: dict[str, int] = Field(default_factory=dict)
    assumption_ids: list[str] = Field(default_factory=list)


class ExposureReport(BaseModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    run_id: UUID
    status: Status
    validation_status: ValidationStatus
    as_of_date: date
    generated_at: datetime
    horizon_months: int = Field(ge=3, le=6)
    existing_hedges_included: Literal[False] = False
    exposures: list[ExposureItem]
    overdue_summary: list[OverdueItem]
    data_quality_issues: list[QualityIssue]
    provenance: Provenance

    @model_validator(mode="after")
    def check_validation_status(self) -> Self:
        severities = {issue.severity for issue in self.data_quality_issues}
        expected = (
            ("FAILED", "FAILED")
            if "ERROR" in severities
            else (("PARTIAL", "WARNINGS") if "WARNING" in severities else ("COMPLETED", "PASSED"))
        )
        if (self.status, self.validation_status) != expected:
            raise ValueError("Report status contradicts its issues")
        if self.status == "FAILED" and (self.exposures or self.overdue_summary):
            raise ValueError("Failed reports must omit amounts")
        return self
