from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from erp_agent.contracts.exposure import Provenance
from erp_agent.contracts.requests import ScenarioName


class SourceEvidence(BaseModel):
    status: Literal["DIRECT", "DERIVED_VERIFIABLY", "MISSING", "UNVERIFIED"]
    header: str | None = None
    note: str


class SourceFileSummary(BaseModel):
    kind: str
    file: str
    sheet: str
    row_count: int = Field(ge=0)
    header_row: int | None = None
    encoding: str | None = None
    is_data_table: bool


class DatasetManifest(BaseModel):
    repository_url: str
    upstream_repo: str
    upstream_commit: str
    git_blob_sha: str
    file_sha256: str
    downloaded_at: datetime
    source_license: str
    pinned_blob_verified: bool
    import_batch_id: UUID
    status: Literal["COMPLETED"] = "COMPLETED"
    source_rows_read: int = Field(ge=0)
    canonical_mapped: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    row_counts: dict[str, int]
    canonical_counts: dict[str, int]
    mapping_version: str
    mapping_hash: str
    files: list[SourceFileSummary]


class DatasetQuality(BaseModel):
    import_batch_id: UUID
    source_rows_read: int = Field(ge=0)
    canonical_mapped: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    quarantine_codes: dict[str, int]
    business_semantics: dict[str, SourceEvidence]
    warnings: list[str]


class ScenarioSummary(BaseModel):
    scenario: ScenarioName
    dataset_version_id: UUID
    scenario_id: str
    scenario_version: str
    as_of_date: date
    first_due_month: date | None = None
    months: int | None = None
    currencies: list[str]
    visible_documents: int = Field(ge=0)


class SourceLocator(BaseModel):
    scenario_record_id: UUID
    source_kind: str
    raw_record_id: UUID | None = None
    canonical_record_id: UUID | None = None
    source_file: str | None = None
    source_sheet: str | None = None
    source_row_number: int | None = None
    source_row_hash: str | None = None
    assumption_ids: list[str]


class ExposureLineage(BaseModel):
    run_id: UUID
    provenance: Provenance
    linked_record_count: int = Field(ge=0)
    records: list[SourceLocator] = Field(default_factory=list)
    truncated: bool = False
