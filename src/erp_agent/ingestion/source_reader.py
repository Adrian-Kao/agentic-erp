import csv
import io
import json
import posixpath
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from zipfile import BadZipFile, ZipFile, ZipInfo

from defusedxml.ElementTree import fromstring
from openpyxl import load_workbook

from erp_agent.ingestion.source_manifest import MAX_ZIP_BYTES, SourceError, identify

SOURCES = {
    "customers": "Master Data/D365_Customers_DMF_Final.csv",
    "vendors": "Master Data/D365_Vendors_DMF_Final.csv",
    "ap": "Transactional Data/AP_Journal_Import_Updated.xlsx",
    "ar": "Transactional Data/AR_Journal_Import_Updated.xlsx",
}
MAX_ROWS, MAX_COLUMNS, MAX_CELL = 20_000, 128, 16_384
MAX_EXPANDED_BYTES = 40 * 1024 * 1024
Cell = str | int | bool | Decimal | datetime | date | None


@dataclass(frozen=True)
class SourceRow:
    number: int
    values: tuple[Cell, ...]
    formulas: tuple[int, ...] = ()
    errors: tuple[int, ...] = ()

    def raw(self, headers: tuple[str, ...]) -> dict[str, Any]:
        return {
            "headers": list(headers),
            "values": list(self.values),
            "formula_columns": list(self.formulas),
            "error_columns": list(self.errors),
        }


@dataclass(frozen=True)
class Sheet:
    kind: str
    file: str
    name: str
    headers: tuple[str, ...]
    header_row: int | None
    rows: tuple[SourceRow, ...]
    duplicate_headers: tuple[str, ...]
    blank_rows: int
    repeated_headers: int
    metadata_rows: int
    encoding: str | None = None
    bom: bool = False
    delimiter: str | None = None


def canonical_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def content_hash(obj: object) -> str:
    return sha256(canonical_json(obj).encode()).hexdigest()


def batch_id(manifest: dict[str, Any]) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"financebenchmark/{manifest['upstream_commit']}/{manifest['file_sha256']}",
        )
    )


def checked_zip(archive: ZipFile) -> list[ZipInfo]:
    entries = archive.infolist()
    if len(entries) > 512 or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
        raise SourceError("SOURCE_ARCHIVE_LIMIT: expanded size or member count")
    seen: set[str] = set()
    for entry in entries:
        name = entry.orig_filename.rstrip("/")
        if (
            not name
            or "\0" in name
            or "\\" in name
            or name.startswith("/")
            or ":" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or name.casefold() in seen
        ):
            raise SourceError("SOURCE_ARCHIVE_UNSAFE_PATH: invalid or duplicate ZIP path")
        seen.add(name.casefold())
        if (
            stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}
            or entry.flag_bits & 1
        ):
            raise SourceError("SOURCE_ARCHIVE_UNSAFE_MEMBER: link, special file or encryption")
        if entry.file_size > 1024 * 1024 and entry.file_size > max(1, entry.compress_size) * 250:
            raise SourceError("SOURCE_ARCHIVE_LIMIT: expansion ratio")
    return entries


def _xlsx_paths(archive: ZipFile) -> dict[str, str]:
    root = fromstring(archive.read("xl/workbook.xml"))
    relations = fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel = {
        item.get("Id"): item.get("Target")
        for item in relations
        if item.get("TargetMode") != "External"
    }
    result: dict[str, str] = {}
    for sheet in root.findall("{*}sheets/{*}sheet"):
        ident = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rel.get(ident)
        if not target:
            raise SourceError("SOURCE_XLSX_UNSAFE_RELATIONSHIP")
        resolved = posixpath.normpath(
            target.lstrip("/") if target.startswith("/") else "xl/" + target
        )
        if not resolved.startswith("xl/worksheets/"):
            raise SourceError("SOURCE_XLSX_UNSAFE_RELATIONSHIP")
        result[sheet.attrib["name"]] = resolved
    return result


def _header(values: tuple[Cell, ...]) -> bool:
    populated = [value for value in values if value is not None]
    words = " ".join(str(value).casefold() for value in populated)
    return (
        len(populated) >= 3
        and all(isinstance(value, str) for value in populated)
        and (len(re.findall(r"account|currency|debit|credit|date|voucher", words)) >= 2)
    )


def _sheet(
    kind: str, file: str, name: str, rows: list[SourceRow], header: int | None, **meta: Any
) -> Sheet:
    blank = sum(not any(value is not None and value != "" for value in row.values) for row in rows)
    if header is None:
        return Sheet(kind, file, name, (), None, (), (), blank, 0, len(rows) - blank, **meta)
    header_values = rows[header].values
    headers = tuple(str(value).strip() if value is not None else "" for value in header_values)
    keys = [value.casefold() for value in headers if value]
    duplicate = tuple(sorted({value for value in keys if keys.count(value) > 1}))
    used: list[SourceRow] = []
    repeated = 0
    for row in rows[header + 1 :]:
        if not any(value is not None and value != "" for value in row.values):
            continue
        if row.values == header_values:
            repeated += 1
            continue
        used.append(row)
    return Sheet(
        kind,
        file,
        name,
        headers,
        rows[header].number,
        tuple(used),
        duplicate,
        blank,
        repeated,
        len(rows) - blank - len(used) - repeated - 1,
        **meta,
    )


def _check_cells(values: tuple[Cell, ...], number: int) -> None:
    if (
        number > MAX_ROWS
        or len(values) > MAX_COLUMNS
        or any(isinstance(value, str) and len(value) > MAX_CELL for value in values)
    ):
        raise SourceError("SOURCE_TABLE_LIMIT: spreadsheet row, column or cell exceeds bounds")


def read_csv(kind: str, file: str, blob: bytes) -> Sheet:
    bom = blob.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"))
    encoding = (
        "utf-16" if blob.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig" if bom else "utf-8"
    )
    value = blob.decode(encoding)
    first = next((line for line in value.splitlines() if line.strip()), "")
    delimiter = csv.Sniffer().sniff(first, delimiters=",;\t|").delimiter
    reader = csv.reader(io.StringIO(value, newline=""), delimiter=delimiter, strict=True)
    records: list[SourceRow] = []
    width = 0
    for row in reader:
        values = tuple(field if field != "" else None for field in row)
        _check_cells(values, reader.line_num)
        if not width and any(values):
            width = len(values)
        if any(values) and len(values) != width:
            raise SourceError("SOURCE_CSV_WIDTH_MISMATCH")
        records.append(SourceRow(reader.line_num, values))
    header = next((index for index, row in enumerate(records) if any(row.values)), None)
    return _sheet(kind, file, "", records, header, encoding=encoding, bom=bom, delimiter=delimiter)


def read_xlsx(kind: str, file: str, blob: bytes) -> list[Sheet]:
    with ZipFile(io.BytesIO(blob)) as archive:
        checked_zip(archive)
        paths = _xlsx_paths(archive)
        workbook = load_workbook(
            io.BytesIO(blob), read_only=True, data_only=False, keep_links=False
        )
        try:
            sheets: list[Sheet] = []
            for ws in workbook:
                if (ws.max_row or 0) > MAX_ROWS or (ws.max_column or 0) > MAX_COLUMNS:
                    raise SourceError("SOURCE_TABLE_LIMIT: XLSX dimensions")
                root = fromstring(archive.read(paths[ws.title]))
                numeric = {
                    node.get("r"): node.findtext("{*}v")
                    for node in root.findall("{*}sheetData/{*}row/{*}c")
                    if node.get("t", "n") == "n" and node.find("{*}f") is None
                }
                rows: list[SourceRow] = []
                for number, cells in enumerate(ws.iter_rows(), 1):
                    values: tuple[Cell, ...] = tuple(
                        Decimal(numeric[cell.coordinate])
                        if isinstance(cell.value, float)
                        and numeric.get(cell.coordinate) is not None
                        else cell.value
                        for cell in cells
                    )
                    _check_cells(values, number)
                    rows.append(
                        SourceRow(
                            number,
                            values,
                            tuple(i for i, c in enumerate(cells) if c.data_type == "f"),
                            tuple(i for i, c in enumerate(cells) if c.data_type == "e"),
                        )
                    )
                header = next((i for i, row in enumerate(rows[:30]) if _header(row.values)), None)
                sheets.append(_sheet(kind, file, ws.title, rows, header))
        finally:
            workbook.close()
    return sheets


def load_source(path: Path, *, require_pin: bool = True) -> tuple[dict[str, Any], list[Sheet]]:
    manifest = identify(path)
    if require_pin and not manifest["pinned_blob_verified"]:
        raise SourceError("SOURCE_BLOB_MISMATCH: expected the pinned Microsoft archive")
    try:
        # Parse the same bytes that were identified, even in the presence of concurrent file changes.
        with path.open("rb") as stream:
            content = stream.read(MAX_ZIP_BYTES + 1)
        if sha256(content).hexdigest() != manifest["file_sha256"]:
            raise SourceError("SOURCE_CHANGED_DURING_READ")
        with ZipFile(io.BytesIO(content)) as archive:
            entries = checked_zip(archive)
            manifest["zip_members"] = [entry.filename for entry in entries]
            manifest["import_batch_id"] = batch_id(manifest)
            sheets: list[Sheet] = []
            for kind, suffix in SOURCES.items():
                parts = PurePosixPath(suffix.casefold()).parts
                matches = [
                    entry
                    for entry in entries
                    if not entry.is_dir()
                    and PurePosixPath(entry.filename.casefold()).parts[-len(parts) :] == parts
                ]
                if len(matches) != 1:
                    raise SourceError(f"SOURCE_MEMBER_MISSING_OR_AMBIGUOUS: {kind}")
                member = matches[0]
                blob = archive.read(member)
                if suffix.endswith(".csv"):
                    sheets.append(read_csv(kind, member.filename, blob))
                else:
                    sheets.extend(read_xlsx(kind, member.filename, blob))
        return manifest, sheets
    except (BadZipFile, UnicodeError, csv.Error, KeyError) as exc:
        raise SourceError("SOURCE_FORMAT_INVALID: ZIP, CSV, or XLSX cannot be decoded") from exc
