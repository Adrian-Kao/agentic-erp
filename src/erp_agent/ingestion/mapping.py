from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import yaml

from erp_agent.ingestion.source_manifest import COMMIT, SourceError
from erp_agent.ingestion.source_reader import Cell, Sheet, SourceRow

MAPPING_PATH = Path("data/mappings/financebenchmark-v1.yaml")
_MASTER = {"account", "name", "currency"}
_JOURNAL = {
    "company",
    "account_type",
    "account",
    "offset_company",
    "journal_date",
    "debit",
    "credit",
    "currency",
    "line_number",
}


class RowError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        text = handle.read(65_537)
    if len(text) > 65_536:
        raise SourceError("DATASET_MAPPING_TOO_LARGE")
    try:
        doc = yaml.safe_load(text)
        if (
            not isinstance(doc, dict)
            or doc.get("source_commit") != COMMIT
            or not doc.get("version")
        ):
            raise SourceError("DATASET_MAPPING_INVALID: pinned commit and version required")
        for kind in ("customers", "vendors", "ap", "ar"):
            entry = doc["tables"][kind]
            fields = entry["fields"]
            if (
                not isinstance(entry["sheet"], str)
                or not isinstance(fields, dict)
                or set(fields) != (_MASTER if kind in {"customers", "vendors"} else _JOURNAL)
                or any(not isinstance(header, str) or not header for header in fields.values())
            ):
                raise SourceError(f"DATASET_MAPPING_INVALID: invalid fields for {kind}")
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        raise SourceError("DATASET_MAPPING_INVALID: invalid mapping document") from exc
    return doc


def mapped_sheets(
    sheets: list[Sheet], mapping: dict[str, Any]
) -> dict[str, tuple[Sheet, dict[str, int]]]:
    result = {}
    for kind, rule in mapping["tables"].items():
        selected = [sheet for sheet in sheets if sheet.kind == kind and sheet.name == rule["sheet"]]
        if len(selected) != 1 or selected[0].header_row is None:
            raise SourceError(f"DATASET_HEADER_MISMATCH: sheet unavailable for {kind}")
        sheet = selected[0]
        if sheet.duplicate_headers or any(
            header not in sheet.headers for header in rule["fields"].values()
        ):
            raise SourceError(f"DATASET_HEADER_MISMATCH: required headers unavailable for {kind}")
        if any(
            candidate.rows
            for candidate in sheets
            if candidate.kind == kind and candidate.name != sheet.name
        ):
            raise SourceError(f"DATASET_HEADER_MISMATCH: unmapped data sheet for {kind}")
        result[kind] = (
            sheet,
            {name: sheet.headers.index(col) for name, col in rule["fields"].items()},
        )
    return result


def row_values(row: SourceRow, indexes: dict[str, int]) -> dict[str, Cell]:
    if (set(row.formulas) | set(row.errors)) & set(indexes.values()):
        raise RowError("UNSAFE_CELL", "Mapped field contains an Excel formula or error")
    return {
        name: row.values[index] if index < len(row.values) else None
        for name, index in indexes.items()
    }


def _text(value: Cell, field: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise RowError(
            "MISSING_OR_INVALID_FIELD", f"Required source field {field} is missing or invalid"
        )
    return value.strip()


def _currency(value: Cell) -> str:
    code = _text(value, "currency", 3)
    if len(code) != 3 or not code.isascii() or not code.isalpha() or not code.isupper():
        raise RowError("INVALID_CURRENCY", "Source currency is invalid")
    return code


def _money(value: Cell) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
        if (
            not amount.is_finite()
            or amount < 0
            or amount > Decimal("9999999999999999.9999")
            or amount.quantize(Decimal(".0001")) != amount
        ):
            raise InvalidOperation
        return amount
    except InvalidOperation as exc:
        raise RowError("INVALID_JOURNAL_AMOUNT", "Invalid decimal journal amount") from exc


def _date(value: Cell) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(_text(value, "journal_date", 10), "%m/%d/%Y").date()
    except ValueError as exc:
        raise RowError("INVALID_JOURNAL_DATE", "Journal date could not be normalized") from exc


def master_values(values: dict[str, Cell]) -> dict[str, Any]:
    return {
        "account": _text(values["account"], "account", 64),
        "display_name": _text(values["name"], "name", 255),
        "currency": _currency(values["currency"]),
    }


def journal_values(
    values: dict[str, Cell], kind: str, masters: dict[tuple[str, str], UUID]
) -> dict[str, Any]:
    master_kind, account_type = ("customers", "Customer") if kind == "ar" else ("vendors", "Vendor")
    if values["account_type"] != account_type:
        raise RowError("INVALID_ACCOUNT_TYPE", "Journal does not reference the expected subledger")
    account = _text(values["account"], "counterparty_account", 64)
    counterparty = masters.get((master_kind, account))
    if counterparty is None:
        raise RowError("ORPHAN_COUNTERPARTY", "Journal account does not match the imported master")
    debit, credit = _money(values["debit"]), _money(values["credit"])
    if bool(debit) == bool(credit):
        raise RowError(
            "AMBIGUOUS_JOURNAL_AMOUNT", "Exactly one nonzero journal-side amount is required"
        )
    line = values["line_number"]
    if not isinstance(line, int) or isinstance(line, bool) or not 0 < line <= 2_147_483_647:
        raise RowError("INVALID_JOURNAL_LINE", "Journal line number is invalid")
    offset = values["offset_company"]
    return {
        "counterparty_id": counterparty,
        "kind": kind.upper(),
        "company": _text(values["company"], "company", 32),
        "offset_company": _text(offset, "offset_company", 32) if offset else None,
        "journal_date": _date(values["journal_date"]),
        "currency": _currency(values["currency"]),
        "debit": debit,
        "credit": credit,
        "line_number": line,
    }


def candidate_master_counts(sheet: Sheet, index: int) -> Counter[str]:
    return Counter(
        str(row.values[index]).strip()
        for row in sheet.rows
        if index < len(row.values) and isinstance(row.values[index], str)
    )


def source_id(batch: UUID, sheet: Sheet, row: SourceRow) -> UUID:
    return uuid5(batch, f"{sheet.file}/{sheet.name}/{row.number}")
