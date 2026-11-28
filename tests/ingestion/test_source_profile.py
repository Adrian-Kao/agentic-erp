from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest
from openpyxl import Workbook

from erp_agent.ingestion.profile import profile
from erp_agent.ingestion.source_manifest import SOURCE_PATH, SourceError
from erp_agent.ingestion.source_reader import checked_zip, load_source, read_csv, read_xlsx


def test_pinned_source_profile() -> None:
    if not SOURCE_PATH.is_file():
        pytest.skip("Run scripts/fetch_microsoft_dataset.py to verify the official dataset")
    identity, sheets = load_source(SOURCE_PATH)
    manifest, columns = profile(identity, sheets)
    assert manifest["pinned_blob_verified"]
    assert (
        manifest["file_sha256"]
        == "5da754beab30462f4df48b93d69345e6d7d09b575ae4304d4bc5569a6572b239"
    )
    assert manifest["row_counts"] == {"customers": 1000, "vendors": 1000, "ap": 3428, "ar": 3459}
    assert [s.name for s in sheets if s.kind == "ap"] == ["Sheet1", "data_cache"]
    assert all(not s.duplicate_headers for s in sheets)
    assert {col["header"] for col in columns if col["kind"] == "ar"} >= {
        "Date",
        "Currency",
        "Debit",
    }


@pytest.mark.parametrize("entry", ["../escape.csv", "/absolute.csv", "C:/escape.csv", "a\\b.csv"])
def test_reject_unsafe_zip_members(entry: str) -> None:
    memory = BytesIO()
    with ZipFile(memory, "w") as archive:
        raw_member = ZipInfo("synthetic.csv")
        raw_member.filename = entry
        archive.writestr(raw_member, "untrusted")
    with ZipFile(memory) as archive, pytest.raises(SourceError, match="UNSAFE_PATH"):
        checked_zip(archive)


def test_missing_source_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="SOURCE_DATA_UNAVAILABLE"):
        load_source(tmp_path / "missing.zip")


def test_utf8_bom_and_multisheet_formulas() -> None:
    customer = read_csv("customers", "source.csv", "\ufeffAccount;Currency\nA;USD\n".encode())
    assert customer.bom and customer.delimiter == ";" and len(customer.rows) == 1
    book = Workbook()
    table = book.active
    table.append(["Journal template"])
    table.append(["Date", "Account", "Currency", "Debit"])
    table.append(["12/30/2025", "ACC", "USD", 123.125])
    table.append(["12/31/2025", "ACC", "USD", "=1+1"])
    book.create_sheet("Blank")
    stream = BytesIO()
    book.save(stream)
    tables = read_xlsx("ap", "source.xlsx", stream.getvalue())
    assert tables[0].header_row == 2 and str(tables[0].rows[0].values[3]) == "123.125"
    assert tables[0].rows[1].formulas == (3,)
    assert tables[1].name == "Blank" and tables[1].header_row is None
