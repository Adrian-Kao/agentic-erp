from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, select

from erp_agent.contracts.datasets import (
    DatasetManifest,
    DatasetQuality,
    ExposureLineage,
    ScenarioSummary,
    SourceLocator,
)
from erp_agent.contracts.errors import ServiceError
from erp_agent.contracts.exposure import ExposureReport
from erp_agent.database import dataset_tables as ds
from erp_agent.database.tables import entities
from erp_agent.security.authorization import Principal


def require_dataset_admin(principal: Principal) -> None:
    if not principal.dataset_admin:
        raise ServiceError(
            "DATASET_ADMIN_REQUIRED", "Dataset administrator access is required", 403
        )


class DatasetService:
    def __init__(self, reader: Engine, writer: Engine) -> None:
        self.reader = reader
        self.writer = writer

    def _current(self) -> dict[str, Any]:
        with self.reader.connect() as conn:
            payload = conn.scalar(
                select(ds.batches.c.manifest_json)
                .join(ds.current_source)
                .where(
                    ds.current_source.c.name == "financebenchmark",
                    ds.batches.c.status == "COMPLETED",
                )
            )
        if not payload or not payload.get("pinned_blob_verified"):
            raise ServiceError(
                "SOURCE_DATA_UNAVAILABLE", "The pinned Microsoft dataset is not active", 503
            )
        return dict(payload)

    def manifest(self, principal: Principal) -> DatasetManifest:
        require_dataset_admin(principal)
        return DatasetManifest.model_validate(self._current())

    def quality(self) -> DatasetQuality:
        data = self._current()
        warnings = sorted(
            key
            for key, item in data["business_semantics"].items()
            if item["status"] in {"MISSING", "UNVERIFIED"}
        )
        return DatasetQuality.model_validate({**data, "warnings": warnings})

    def scenarios(self, principal: Principal) -> list[ScenarioSummary]:
        version = ds.versions.c
        documents = ds.scenario_documents.c
        scoped_docs = (
            select(documents.version_id, func.count().label("visible"))
            .join(entities, documents.entity_id == entities.c.id)
            .where(
                documents.tenant_id == principal.tenant_id,
                entities.c.tenant_id == principal.tenant_id,
                entities.c.is_active.is_(True),
                entities.c.entity_code.in_(principal.entity_ids),
            )
            .group_by(documents.version_id)
            .subquery()
        )
        statement = (
            select(ds.versions, scoped_docs.c.visible)
            .select_from(
                ds.active_scenarios.join(ds.versions).join(
                    scoped_docs, scoped_docs.c.version_id == version.id
                ),
            )
            .order_by(version.scenario)
        )
        with self.reader.connect() as conn:
            return [
                ScenarioSummary.model_validate(
                    {
                        "scenario": row.scenario,
                        "dataset_version_id": row.id,
                        "scenario_id": row.scenario_id,
                        "scenario_version": row.scenario_version,
                        "as_of_date": row.as_of_date,
                        "first_due_month": row.config_json.get("first_due_month"),
                        "months": row.config_json.get("months"),
                        "currencies": row.config_json["currencies"],
                        "visible_documents": row.visible,
                    }
                )
                for row in conn.execute(statement)
            ]

    def lineage(
        self, report: ExposureReport, principal: Principal, *, include_records: bool, limit: int
    ) -> ExposureLineage:
        if include_records:
            require_dataset_admin(principal)
        count = sum(report.provenance.participating_source_kinds.values())
        answer = ExposureLineage(
            run_id=report.run_id, provenance=report.provenance, linked_record_count=count
        )
        if not include_records or not count:
            return answer
        with self.writer.connect() as conn:
            identifiers: list[UUID] = list(
                conn.scalars(
                    select(ds.run_source_links.c.scenario_record_id)
                    .where(ds.run_source_links.c.run_id == report.run_id)
                    .order_by(ds.run_source_links.c.scenario_record_id)
                    .limit(limit)
                )
            )
        doc, link = ds.scenario_documents.c, ds.source_links.c
        query = (
            select(
                doc.id.label("scenario_record_id"),
                doc.source_kind,
                doc.assumption_ids,
                link.original_import_record_id.label("raw_record_id"),
                link.canonical_record_id,
                link.source_file,
                link.source_sheet,
                link.source_row_number,
                link.source_row_hash,
            )
            .select_from(
                ds.scenario_documents.outerjoin(
                    ds.source_links, link.scenario_record_id == doc.id
                ).join(entities, entities.c.id == doc.entity_id)
            )
            .where(
                doc.id.in_(identifiers),
                doc.tenant_id == principal.tenant_id,
                entities.c.tenant_id == principal.tenant_id,
                entities.c.entity_code.in_(principal.entity_ids),
            )
            .order_by(doc.id)
        )
        with self.reader.connect() as conn:
            records = [SourceLocator.model_validate(row) for row in conn.execute(query).mappings()]
        return answer.model_copy(update={"records": records, "truncated": count > len(records)})
