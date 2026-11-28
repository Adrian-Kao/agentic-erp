import csv
import json
import re
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from erp_agent.ingestion.source_reader import Cell, Sheet, canonical_json

NUMERIC = re.compile(r"^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")


def value_type(value: Cell) -> str:
    if value is None:
        return "blank"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (date, datetime)):
        return "date"
    if isinstance(value, (Decimal, int)) or (isinstance(value, str) and NUMERIC.fullmatch(value)):
        return "number"
    return "text"


def column_profile(sheet: Sheet) -> list[dict[str, Any]]:
    count = len(sheet.rows)
    result: list[dict[str, Any]] = []
    for i, header in enumerate(sheet.headers):
        values = [row.values[i] if i < len(row.values) else None for row in sheet.rows]
        types = Counter(value_type(value) for value in values)
        formulas = sum(i in row.formulas for row in sheet.rows)
        errors = sum(i in row.errors for row in sheet.rows)
        numbers: list[Decimal] = []
        for value in values:
            if value_type(value) == "number":
                try:
                    number = Decimal(str(value))
                    if number.is_finite():
                        numbers.append(number)
                    else:
                        errors += 1
                except InvalidOperation:
                    errors += 1
        currency = (
            sorted(
                {
                    value
                    for value in values
                    if isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value)
                }
            )
            if "curr" in header.casefold()
            else []
        )
        result.append(
            {
                "kind": sheet.kind,
                "file": sheet.file,
                "sheet": sheet.name,
                "header_row": sheet.header_row,
                "column": i + 1,
                "header": header,
                "rows": count,
                "null_count": types["blank"],
                "null_rate": round(types["blank"] / count, 6) if count else None,
                "unique_nonnull": len({canonical_json(v) for v in values if v is not None}),
                "types": dict(types),
                "formula_count": formulas,
                "numeric_or_cell_error_count": errors,
                "negative_numeric_count": sum(number < 0 for number in numbers),
                "date_candidate": "date" in header.casefold() or bool(types["date"]),
                "amount_candidate": bool(
                    re.search(r"amount|debit|credit", header, re.I) and numbers
                ),
                "currency_candidates": currency,
                "redacted_first_three": [
                    f"<{value_type(value)}>" if value is not None else None for value in values[:3]
                ],
            }
        )
    return result


def profile(
    manifest: dict[str, Any], sheets: list[Sheet]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    descriptions = []
    profiles: list[dict[str, Any]] = []
    for sheet in sheets:
        columns = column_profile(sheet)
        profiles.extend(columns)
        descriptions.append(
            {
                "kind": sheet.kind,
                "file": sheet.file,
                "sheet": sheet.name,
                "is_data_table": sheet.header_row is not None,
                "header_row": sheet.header_row,
                "headers": list(sheet.headers),
                "row_count": len(sheet.rows),
                "blank_rows": sheet.blank_rows,
                "metadata_rows": sheet.metadata_rows,
                "repeated_headers": sheet.repeated_headers,
                "duplicate_headers": list(sheet.duplicate_headers),
                "encoding": sheet.encoding,
                "bom": sheet.bom if sheet.encoding else None,
                "delimiter": sheet.delimiter,
                "formula_columns": [col["header"] for col in columns if col["formula_count"]],
                "date_candidates": [col["header"] for col in columns if col["date_candidate"]],
                "amount_candidates": [col["header"] for col in columns if col["amount_candidate"]],
                "currency_candidates": sorted(
                    {code for col in columns for code in col["currency_candidates"]}
                ),
            }
        )
    row_counts = {
        kind: sum(len(s.rows) for s in sheets if s.kind == kind)
        for kind in ("customers", "vendors", "ap", "ar")
    }
    return (
        {
            **manifest,
            "files": descriptions,
            "row_counts": row_counts,
            "source_rows_read": sum(row_counts.values()),
        },
        profiles,
    )


def write_profile(
    output: Path,
    manifest: dict[str, Any],
    columns: list[dict[str, Any]],
    semantics: Mapping[str, Any] | None = None,
) -> str:
    output.mkdir(parents=True, exist_ok=True)
    manifest = {**manifest, "business_semantics": dict(semantics or {})}
    (output / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "source_columns.csv").open("w", encoding="utf-8", newline="") as handle:
        if columns:
            writer = csv.DictWriter(handle, fieldnames=list(columns[0]))
            writer.writeheader()
            for column in columns:
                writer.writerow(
                    {
                        key: canonical_json(value) if isinstance(value, (dict, list)) else value
                        for key, value in column.items()
                    }
                )
    lines = [
        "# Microsoft FinanceBenchmark 來源盤點",
        "",
        f"- 固定 commit `{manifest['upstream_commit']}`",
        f"- Git blob `{manifest['git_blob_sha']}`（verified={manifest['pinned_blob_verified']}）",
        f"- ZIP SHA256 `{manifest['file_sha256']}`",
        f"- 共 {manifest['source_rows_read']:,} 筆有效資料列。輔助工作表與範本說明列分開統計。",
        "- 原始素材為 Dynamics 365 Finance 的公開合成資料，授權為 MIT。",
        "",
        "| 類別 | 工作表 | 有效資料列 | Header 列 | 空白／說明／重複 header | 編碼／BOM／分隔符 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for entry in manifest["files"]:
        encoding = (
            f"{entry['encoding']} / {entry['bom']} / {entry['delimiter']}"
            if entry["encoding"]
            else "XLSX"
        )
        lines.append(
            f"| {entry['kind']} | {entry['sheet'] or 'CSV'} | {entry['row_count']} | "
            f"{entry['header_row'] or '—'} | "
            f"{entry['blank_rows']} / {entry['metadata_rows']} / {entry['repeated_headers']} | {encoding} |"
        )
    for entry in manifest["files"]:
        lines.extend(
            (
                "",
                f"## {entry['kind']} · {entry['sheet'] or 'CSV'}",
                "",
                f"來源 `{entry['file']}`",
                "",
                "- 欄位 "
                + (
                    ", ".join(f"`{name or '(blank)'}`" for name in entry["headers"]) or "無交易表頭"
                ),
                "- 日期候選 " + (", ".join(entry["date_candidates"]) or "無"),
                "- 金額候選 " + (", ".join(entry["amount_candidates"]) or "無"),
                "- 幣別候選 " + (", ".join(entry["currency_candidates"]) or "無"),
                "- 公式／重複欄位 "
                + (", ".join(entry["formula_columns"] + entry["duplicate_headers"]) or "無"),
            )
        )
    if semantics:
        lines.extend(("", "## 業務語意", ""))
        for field, evidence in semantics.items():
            lines.append(f"- `{field}` — **{evidence['status']}**。{evidence['note']}")
    lines.extend(
        (
            "",
            "逐欄空值率、唯一值、型別、異常及前三筆去識別樣本見 "
            "`data/profiling/source_columns.csv`。",
            "",
        )
    )
    report = "\n".join(lines)
    (output / "profile_report.md").write_text(report, encoding="utf-8")
    return report
