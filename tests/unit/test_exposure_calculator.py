from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from erp_agent.contracts.requests import ExposureRequest
from erp_agent.database.repository import Allocation, Entity, Invoice, Snapshot
from erp_agent.domain.date_buckets import month_window
from erp_agent.domain.exposure_calculator import calculate
from erp_agent.domain.validation import validate_exposure
from scripts.seed_demo import AS_OF

ENTITIES = (
    Entity(UUID(int=1), "TW01", "Company A", "TWD"),
    Entity(UUID(int=2), "TW02", "Company B", "TWD"),
)
REQUEST = ExposureRequest(entity_ids=["TW01", "TW02"], as_of_date=AS_OF, horizon_months=3)
AR = Invoice(
    UUID(int=10),
    ENTITIES[0].id,
    "AR",
    "USD",
    date(2026, 10, 1),
    date(2027, 1, 15),
    Decimal("1400000"),
    "OPEN",
)
AP = replace(AR, id=UUID(int=11), kind="AP", original_amount=Decimal("800000"))
PAY = Allocation(UUID(int=21), AR.id, "AR", "USD", Decimal("200000"), AS_OF, "POSTED")


@pytest.mark.parametrize(
    ("docs", "payments", "expected", "direction"),
    [
        ((AR, AP), (PAY,), "400000.0000", "NET_INFLOW"),
        ((replace(AP, original_amount=Decimal("500000")),), (), "-500000.0000", "NET_OUTFLOW"),
        ((replace(AR, original_amount=AP.original_amount), AP), (), "0.0000", "BALANCED"),
        ((AR,), (PAY,), "1200000.0000", "NET_INFLOW"),
        (
            (AR,),
            (replace(PAY, effective_date=AS_OF + timedelta(days=1)),),
            "1400000.0000",
            "NET_INFLOW",
        ),
        (
            (replace(AR, original_amount=Decimal("9999999999999999.9999")),),
            (),
            "9999999999999999.9999",
            "NET_INFLOW",
        ),
        ((replace(AR, due_date=AS_OF),), (PAY,), "1200000.0000", "NET_INFLOW"),
    ],
)
def test_decimal_directions(docs, payments, expected, direction) -> None:
    snapshot = Snapshot(ENTITIES, docs, payments)
    report = calculate(snapshot, REQUEST)
    assert report.exposures[0].direction == direction
    assert report.model_dump(mode="json")["exposures"][0]["net_exposure"] == expected
    validate_exposure(report, snapshot, REQUEST)


def test_currency_entity_and_functional_currency() -> None:
    docs = (
        AR,
        replace(AP, currency="JPY"),
        replace(AR, id=UUID(int=12), entity_id=ENTITIES[1].id),
        replace(AR, id=UUID(int=13), currency="TWD"),
    )
    assert [
        (r.entity_id, r.currency)
        for r in calculate(Snapshot(ENTITIES, docs, ()), REQUEST).exposures
    ] == [
        ("TW01", "JPY"),
        ("TW01", "USD"),
        ("TW02", "USD"),
    ]


def test_settled_cancelled_overdue_and_missing() -> None:
    docs = (
        replace(AR, status="SETTLED"),
        replace(AP, status="CANCELLED"),
        replace(AR, id=UUID(int=12), due_date=date(2026, 11, 26)),
        replace(AR, id=UUID(int=13), due_date=None),
    )
    report = calculate(
        Snapshot(ENTITIES, docs, (replace(PAY, amount=AR.original_amount),)), REQUEST
    )
    assert not report.exposures
    assert report.status == "PARTIAL"
    assert report.overdue_summary[0].overdue_ar == AR.original_amount
    assert report.data_quality_issues[0].code == "MISSING_DUE_DATE"


@pytest.mark.parametrize(
    ("doc", "payment", "code"),
    [
        (AR, replace(PAY, amount=Decimal("1400000.0001")), "OVERPAYMENT"),
        (replace(AR, currency="FAK"), None, "INVALID_INVOICE_SCOPE_OR_CURRENCY"),
        (replace(AR, original_amount=Decimal("-1")), None, "INVALID_INVOICE_AMOUNT"),
        (replace(AR, status="SETTLED"), PAY, "SETTLED_WITH_BALANCE"),
        (AR, replace(PAY, currency="EUR"), "ALLOCATION_MISMATCH"),
    ],
)
def test_errors_fail_closed(doc, payment, code) -> None:
    report = calculate(Snapshot(ENTITIES, (doc, AP), (payment,) if payment else ()), REQUEST)
    assert (report.status, report.validation_status) == ("FAILED", "FAILED")
    assert not report.exposures and not report.overdue_summary
    assert code in {issue.code for issue in report.data_quality_issues}


def test_cross_year_window_and_tamper_detection() -> None:
    assert month_window(AS_OF, 3) == (date(2026, 11, 1), date(2027, 2, 1))
    assert month_window(AS_OF, 6) == (date(2026, 11, 1), date(2027, 5, 1))
    snapshot = Snapshot(ENTITIES, (AR,), (PAY,))
    report = calculate(snapshot, REQUEST)
    changed = report.model_copy(update={"exposures": []})
    with pytest.raises(ValueError, match="reconcile"):
        validate_exposure(changed, snapshot, REQUEST)
