import argparse
from datetime import UTC, datetime
from pathlib import Path

import httpx

from erp_agent.ingestion.source_manifest import (
    MAX_ZIP_BYTES,
    ROOT,
    SOURCE_PATH,
    ZIP_URL,
    SourceError,
    identify,
    identify_bytes,
)


def download(client: httpx.Client, url: str, limit: int) -> bytes:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        content = bytearray()
        for block in response.iter_bytes():
            content.extend(block)
            if len(content) > limit:
                raise SourceError("SOURCE_DOWNLOAD_TOO_LARGE")
    return bytes(content)


def fetch(path: Path, client: httpx.Client) -> dict:
    if path.exists():
        manifest = identify(path)
        if not manifest["pinned_blob_verified"]:
            raise SourceError("SOURCE_BLOB_MISMATCH: existing ZIP preserved")
        return manifest
    try:
        data = download(client, ZIP_URL, MAX_ZIP_BYTES)
    except httpx.HTTPError as exc:
        raise SourceError("SOURCE_DATA_UNAVAILABLE: download the pinned ZIP manually") from exc
    manifest = identify_bytes(data, datetime.now(UTC))
    if not manifest["pinned_blob_verified"]:
        raise SourceError("SOURCE_BLOB_MISMATCH: downloaded ZIP rejected")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and fetch the pinned Microsoft ZIP")
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            manifest = fetch(args.source, client)
            license_path = args.source.with_name("FinanceBenchmark-LICENSE.txt")
            if not license_path.exists():
                try:
                    notice = download(client, f"{ROOT}/LICENSE", 64_000)
                except httpx.HTTPError as exc:
                    raise SourceError(
                        "SOURCE_LICENSE_UNAVAILABLE: retain the pinned MIT notice"
                    ) from exc
                with license_path.open("xb") as stream:
                    stream.write(notice)
    except SourceError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Verified {manifest['bytes']} bytes, SHA256 {manifest['file_sha256']}")


if __name__ == "__main__":
    main()
