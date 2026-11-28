from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from json import dumps
from uuid import UUID, uuid4

from erp_agent.contracts.exposure import (
    ZERO,
    ExposureItem,
    ExposureReport,
    OverdueItem,
    Provenance,
    QualityIssue,
    direction,
)
from erp_agent.contracts.requests import ExposureRequest
from erp_agent.database.repository import Snapshot
from erp_agent.domain.cashflow_rules import MAX_MONEY, OpenInvoice, assess
from erp_agent.domain.date_buckets import month_window


def content_hash(value: object) -> str:
    return "sha256:" + sha256(dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Totals:
    ar: Decimal = ZERO
    ap: Decimal = ZERO
    ar_count: int = 0
    ap_count: int = 0

    def add(self, doc: OpenInvoice) -> None:
        if doc.invoice.kind == "AR":
            self.ar += doc.amount
            self.ar_count += 1
        else:
            self.ap += doc.amount
            self.ap_count += 1


def calculate(
    snapshot: Snapshot,
    request: ExposureRequest,
    *,
    run_id: UUID | None = None,
    generated_at: datetime | None = None,
) -> ExposureReport:
    docs, issues = assess(snapshot, request.as_of_date)
    groups: dict[tuple[str, str, str, str], Totals] = defaultdict(Totals)
    overdue: dict[tuple[str, str], Totals] = defaultdict(Totals)
    _, horizon_end = month_window(request.as_of_date, request.horizon_months)
    for doc in docs:
        invoice = doc.invoice
        due = invoice.due_date
        if (
            due is None
            or invoice.currency == doc.entity.functional_currency
            or (request.currencies and invoice.currency not in request.currencies)
            or doc.entity.entity_code not in request.entity_ids
        ):
            continue
        key = doc.entity.entity_code, invoice.currency
        if due < request.as_of_date:
            if request.include_overdue_summary:
                overdue[key].add(doc)
        elif due < horizon_end:
            groups[(*key, due.strftime("%Y-%m"), doc.entity.functional_currency)].add(doc)

    if any(max(total.ar, total.ap) > MAX_MONEY for total in (*groups.values(), *overdue.values())):
        issues.append(
            QualityIssue(
                code="MONEY_OVERFLOW",
                message="Group exceeds report money precision",
                severity="ERROR",
            )
        )
    failed = any(issue.severity == "ERROR" for issue in issues)
    warned = any(issue.severity == "WARNING" for issue in issues)
    exposures = (
        []
        if failed
        else [
            ExposureItem(
                entity_id=code,
                currency=currency,
                period=period,
                functional_currency=base,
                ar_open=t.ar,
                ap_open=t.ap,
                net_exposure=t.ar - t.ap,
                direction=direction(t.ar - t.ap),
                source_ar_count=t.ar_count,
                source_ap_count=t.ap_count,
            )
            for (code, currency, period, base), t in sorted(groups.items())
        ]
    )
    overdue_items = (
        []
        if failed
        else [
            OverdueItem(
                entity_id=code,
                currency=currency,
                overdue_ar=t.ar,
                overdue_ap=t.ap,
                record_count=t.ar_count + t.ap_count,
            )
            for (code, currency), t in sorted(overdue.items())
        ]
    )
    dataset = snapshot.dataset
    origins = [doc.origin for doc in snapshot.invoices if doc.origin is not None]
    source_kinds = Counter(origin.source_kind for origin in origins)
    return ExposureReport(
        run_id=run_id or uuid4(),
        generated_at=generated_at or datetime.now(UTC),
        status="FAILED" if failed else "PARTIAL" if warned else "COMPLETED",
        validation_status="FAILED" if failed else "WARNINGS" if warned else "PASSED",
        as_of_date=request.as_of_date,
        horizon_months=request.horizon_months,
        exposures=exposures,
        overdue_summary=overdue_items,
        data_quality_issues=issues,
        provenance=Provenance(
            source_system=snapshot.source_system,
            data_version=snapshot.data_version,
            input_hash=content_hash(
                {
                    "snapshot": {
                        key: sorted(value, key=lambda entry: str(entry["id"]))
                        if isinstance(value, tuple)
                        else value
                        for key, value in asdict(snapshot).items()
                    },
                    "request": request.model_dump(mode="json"),
                }
            ),
            source_entity_count=len(snapshot.entities),
            source_ar_count=sum(doc.kind == "AR" for doc in snapshot.invoices),
            source_ap_count=sum(doc.kind == "AP" for doc in snapshot.invoices),
            source_allocation_count=len(snapshot.allocations),
            upstream_repo=dataset.upstream_repo if dataset else None,
            upstream_commit=dataset.upstream_commit if dataset else None,
            source_sha256=dataset.source_sha256 if dataset else None,
            import_batch_id=dataset.import_batch_id if dataset else None,
            dataset_version_id=dataset.version_id if dataset else None,
            scenario=dataset.scenario if dataset else None,
            scenario_id=dataset.scenario_id if dataset else None,
            scenario_version=dataset.scenario_version if dataset else None,
            scenario_seed=dataset.scenario_seed if dataset else None,
            scenario_config_hash=dataset.config_hash if dataset else None,
            source_rows_read=dataset.source_rows_read if dataset else 0,
            canonical_mapped=dataset.canonical_mapped if dataset else 0,
            quarantined=dataset.quarantined if dataset else 0,
            scenario_rows_derived=source_kinds["DEMO_DERIVED_FROM_MICROSOFT"],
            synthetic_only_rows=source_kinds["DEMO_SYNTHETIC"],
            source_reference_count=len(
                {o.source_record_ref for o in origins if o.source_record_ref is not None}
            ),
            missing_required_fields=list(dataset.missing_required_fields) if dataset else [],
            participating_source_kinds=dict(source_kinds),
            assumption_ids=sorted({a for o in origins for a in o.assumptions}),
        ),
    )
