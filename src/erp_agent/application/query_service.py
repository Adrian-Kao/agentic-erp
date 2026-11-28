import json
from datetime import date
from importlib.resources import files
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from erp_agent.contracts.errors import ServiceError
from erp_agent.contracts.exposure import Money, QualityIssue
from erp_agent.contracts.requests import QueryFilters
from erp_agent.database.named_queries import QUERY_NAMES
from erp_agent.database.repository import ERPAdapter
from erp_agent.domain.cashflow_rules import assess
from erp_agent.security.authorization import Principal


class EntityRow(BaseModel):
    entity_id: str
    name: str
    functional_currency: str


class InvoiceRow(BaseModel):
    record_ref: str
    entity_id: str
    currency: str
    due_date: date | None
    open_amount: Money
    status: str


class LineageRow(BaseModel):
    record_ref: str
    entity_id: str
    source_kind: str
    source_record_ref: UUID | None
    canonical_record_id: UUID | None
    assumption_ids: list[str]


class QueryResult(BaseModel):
    rows: list[EntityRow | InvoiceRow | LineageRow]
    truncated: bool = False
    issues: list[QualityIssue]


class QueryService:
    def __init__(self, repository: ERPAdapter) -> None:
        self.repository = repository

    def schema(self, topic: str | None = None) -> dict[str, Any]:
        registry: dict[str, Any] = json.loads(
            files("erp_agent")
            .joinpath("database", "schema_registry.json")
            .read_text(encoding="utf-8")
        )
        if topic is None:
            return registry
        if topic not in {"exposure", "invoices", "queries", "dataset"}:
            raise ServiceError("UNKNOWN_SCHEMA_TOPIC", "Unknown schema topic", 422)
        return {topic: registry[topic], "authorization": registry["authorization"]}

    def entities(self, principal: Principal, requested: list[str] | None = None) -> list[EntityRow]:
        scope = principal.scope(requested)
        rows = self.repository.list_entities(principal.tenant_id, scope)
        if requested is not None and {row.entity_code for row in rows} != set(scope):
            raise ServiceError(
                "ENTITY_FORBIDDEN", "One or more entities are outside your scope", 403
            )
        return [
            EntityRow(
                entity_id=e.entity_code, name=e.name, functional_currency=e.functional_currency
            )
            for e in rows
        ]

    def named_query(
        self, query_name: str, filters: QueryFilters, limit: int, principal: Principal
    ) -> QueryResult:
        if query_name not in QUERY_NAMES:
            raise ServiceError("QUERY_NOT_ALLOWED", "Query name is not allowlisted", 422)
        if not 1 <= limit <= 100:
            raise ServiceError("INVALID_LIMIT", "Limit must be between 1 and 100", 422)
        rows: list[EntityRow | InvoiceRow | LineageRow]
        if query_name == "entity_list":
            if filters.as_of_date or filters.currencies or filters.scenario:
                raise ServiceError("INVALID_FILTERS", "Entity query accepts only entity_ids", 422)
            rows = list(self.entities(principal, filters.entity_ids))
            return QueryResult(rows=rows[:limit], truncated=len(rows) > limit, issues=[])
        if filters.as_of_date is None:
            raise ServiceError("INVALID_FILTERS", "Invoice queries require as_of_date", 422)
        scope = principal.scope(filters.entity_ids)
        snapshot = self.repository.snapshot(
            principal.tenant_id,
            scope,
            filters.as_of_date,
            tuple(filters.currencies or ()),
            filters.scenario,
        )
        if filters.entity_ids and {e.entity_code for e in snapshot.entities} != set(scope):
            raise ServiceError(
                "ENTITY_FORBIDDEN", "One or more entities are outside your scope", 403
            )
        documents, issues = assess(snapshot, filters.as_of_date)
        if query_name == "source_lineage":
            entity_codes = {entity.id: entity.entity_code for entity in snapshot.entities}
            rows = [
                LineageRow(
                    record_ref=f"{doc.kind}:{doc.id}",
                    entity_id=entity_codes[doc.entity_id],
                    source_kind=doc.origin.source_kind,
                    source_record_ref=doc.origin.source_record_ref,
                    canonical_record_id=doc.origin.canonical_record_id,
                    assumption_ids=list(doc.origin.assumptions),
                )
                for doc in snapshot.invoices
                if doc.origin is not None
            ]
            return QueryResult(
                rows=rows[:limit],
                truncated=len(rows) > limit or len(issues) > 100,
                issues=issues[:100],
            )
        if any(issue.severity == "ERROR" for issue in issues):
            return QueryResult(rows=[], issues=issues[:100])
        kind = query_name[-2:].upper()
        rows = [
            InvoiceRow(
                record_ref=f"{kind}:{doc.invoice.id}",
                entity_id=doc.entity.entity_code,
                currency=doc.invoice.currency,
                due_date=doc.invoice.due_date,
                open_amount=doc.amount,
                status=doc.invoice.status,
            )
            for doc in documents
            if doc.invoice.kind == kind
            and (
                not query_name.startswith("overdue")
                or (doc.invoice.due_date is not None and doc.invoice.due_date < filters.as_of_date)
            )
        ]
        return QueryResult(
            rows=rows[:limit], truncated=len(rows) > limit or len(issues) > 100, issues=issues[:100]
        )
