from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from erp_agent.contracts.exposure import ZERO, QualityIssue
from erp_agent.contracts.requests import CURRENCIES
from erp_agent.database.repository import Entity, Invoice, Snapshot

MAX_MONEY = Decimal("9999999999999999.9999")
QUANTUM = Decimal("0.0001")


def valid_amount(amount: Decimal) -> bool:
    return amount.is_finite() and ZERO <= amount <= MAX_MONEY and amount == amount.quantize(QUANTUM)


@dataclass(frozen=True)
class OpenInvoice:
    invoice: Invoice
    entity: Entity
    amount: Decimal


def assess(snapshot: Snapshot, as_of: date) -> tuple[list[OpenInvoice], list[QualityIssue]]:
    entities = {entity.id: entity for entity in snapshot.entities}
    issues: dict[tuple[str, str], QualityIssue] = {}
    open_docs: list[OpenInvoice] = []
    balances: dict[tuple[str, UUID], Decimal] = defaultdict(lambda: ZERO)
    invoices = [doc for doc in snapshot.invoices if doc.issue_date <= as_of]
    payments = [payment for payment in snapshot.allocations if payment.effective_date <= as_of]
    docs = {(doc.kind, doc.id): doc for doc in invoices}

    def issue(
        code: str, message: str, ref: str, severity: Literal["WARNING", "ERROR"] = "ERROR"
    ) -> None:
        issues[code, ref] = QualityIssue(
            code=code, severity=severity, message=message, record_ref=ref
        )

    for company in snapshot.entities:
        if company.functional_currency not in CURRENCIES:
            issue(
                "INVALID_FUNCTIONAL_CURRENCY",
                "Unsupported functional currency",
                f"entity:{company.id}",
            )
    counts = Counter(payment.id for payment in payments)
    for payment in payments:
        key = payment.kind, payment.document_id
        doc = docs.get(key)
        ref = f"payment:{payment.id}"
        if counts[payment.id] > 1:
            issue("DUPLICATE_ALLOCATION", "Allocation identity is repeated", ref)
        elif (
            doc is None
            or doc.currency != payment.currency
            or payment.effective_date < doc.issue_date
        ):
            issue("ALLOCATION_MISMATCH", "Allocation does not match its invoice", ref)
        elif not valid_amount(payment.amount) or not payment.amount:
            issue("INVALID_PAYMENT_AMOUNT", "Allocation amount must be positive", ref)
        elif payment.status == "POSTED":
            balances[key] += payment.amount
        elif payment.status != "VOID":
            issue("INVALID_PAYMENT_STATUS", "Unknown allocation status", ref)
    if len(docs) != len(invoices):
        issue("DUPLICATE_INVOICE", "Invoice identity is repeated", "snapshot")
    external_counts = Counter(
        (doc.entity_id, doc.kind, doc.origin.external_document_no)
        for doc in invoices
        if doc.origin and doc.origin.external_document_no
    )
    for doc in invoices:
        ref = f"{doc.kind}:{doc.id}"
        entity = entities.get(doc.entity_id)
        external = doc.origin.external_document_no if doc.origin else None
        if external and external_counts[doc.entity_id, doc.kind, external] > 1:
            issue("DUPLICATE_EXTERNAL_DOCUMENT", "External document number is repeated", ref)
        if not doc.currency:
            issue("MISSING_CURRENCY", "Invoice currency is missing", ref)
        elif entity is None or doc.currency not in CURRENCIES:
            issue(
                "INVALID_INVOICE_SCOPE_OR_CURRENCY",
                "Invoice currency or entity is unsupported",
                ref,
            )
        elif not valid_amount(doc.original_amount):
            issue("INVALID_INVOICE_AMOUNT", "Invalid invoice amount or precision", ref)
        else:
            paid = balances[doc.kind, doc.id]
            remaining = doc.original_amount - paid
            if remaining < ZERO:
                issue("OVERPAYMENT", "Posted allocations exceed the original amount", ref)
            elif doc.status == "SETTLED" and remaining != ZERO:
                issue("SETTLED_WITH_BALANCE", "Settled invoice has an open balance", ref)
            elif doc.status == "PARTIALLY_PAID" and (paid == ZERO or remaining == ZERO):
                issue("INCONSISTENT_PARTIAL_PAYMENT", "Partial status contradicts allocations", ref)
            elif doc.status in {"CANCELLED", "VOID"} and paid:
                issue(
                    "INACTIVE_WITH_PAYMENT", "Cancelled or void invoice has a posted payment", ref
                )
            elif doc.status not in {"OPEN", "PARTIALLY_PAID", "SETTLED", "CANCELLED", "VOID"}:
                issue("INVALID_INVOICE_STATUS", "Unknown invoice status", ref)
            elif doc.status in {"OPEN", "PARTIALLY_PAID"} and remaining > ZERO:
                if doc.due_date is None:
                    issue(
                        "MISSING_DUE_DATE",
                        "Open invoice with no due date was excluded",
                        ref,
                        "WARNING",
                    )
                elif doc.due_date < doc.issue_date:
                    issue("INVALID_DUE_DATE", "Due date precedes invoice issue date", ref)
                open_docs.append(OpenInvoice(doc, entity, remaining))
    return open_docs, [issues[key] for key in sorted(issues)]
