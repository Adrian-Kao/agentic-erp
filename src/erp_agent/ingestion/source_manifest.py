from datetime import UTC, datetime
from hashlib import sha1, sha256
from pathlib import Path

REPO = "microsoft/FinanceBenchmark"
COMMIT = "6ced62b961d4c18b2ba53f268b443eb852fb73ca"
BLOB = "8f884ba254411a2a8df0773baad635ff195637ad"
ROOT = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}"
ZIP_URL = f"{ROOT}/data/fno_benchmark_data_raw/FO%20Benchmark_Data.zip"
SOURCE_PATH = Path("data/source/FO Benchmark_Data.zip")
MAX_ZIP_BYTES = 20 * 1024 * 1024


class SourceError(ValueError):
    """Rejected FinanceBenchmark source with a safe, actionable message."""


def identify(path: Path) -> dict:
    if not path.is_file():
        raise SourceError("SOURCE_DATA_UNAVAILABLE: put the pinned Microsoft ZIP in data/source/")
    with path.open("rb") as stream:
        content = stream.read(MAX_ZIP_BYTES + 1)
    return identify_bytes(content, datetime.fromtimestamp(path.stat().st_mtime, UTC))


def identify_bytes(data: bytes, acquired: datetime) -> dict:
    if len(data) > MAX_ZIP_BYTES:
        raise SourceError("SOURCE_ZIP_TOO_LARGE")
    blob = sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()
    return {
        "repository_url": f"https://github.com/{REPO}",
        "upstream_repo": REPO,
        "upstream_commit": COMMIT,
        "git_blob_sha": blob,
        "pinned_blob_verified": blob == BLOB,
        "file_sha256": sha256(data).hexdigest(),
        "downloaded_at": acquired.isoformat(),
        "source_license": "MIT",
        "bytes": len(data),
    }
