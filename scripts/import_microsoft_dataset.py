import argparse
import json
from pathlib import Path

from erp_agent.config import Settings
from erp_agent.database.engine import database_engine
from erp_agent.ingestion.load_mysql import import_source
from erp_agent.ingestion.mapping import MAPPING_PATH, read_mapping
from erp_agent.ingestion.profile import profile
from erp_agent.ingestion.source_manifest import COMMIT, REPO, SourceError
from erp_agent.ingestion.source_reader import load_source


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(
        description="Import the pinned Microsoft raw and canonical data"
    )
    parser.add_argument("--source", type=Path, default=settings.dataset_zip_path)
    parser.add_argument("--mapping", type=Path, default=MAPPING_PATH)
    args = parser.parse_args()
    try:
        if settings.app_env != "development":
            raise SourceError("DATASET_IMPORT_DISABLED: development environment required")
        if (settings.dataset_repo, settings.dataset_commit) != (REPO, COMMIT):
            raise SourceError(
                "DATASET_PIN_MISMATCH: review the source and mapping before changing the pin"
            )
        identity, sheets = load_source(args.source)
        manifest, _ = profile(identity, sheets)
        engine = database_engine(settings.import_url)
        try:
            result = import_source(engine, manifest, sheets, read_mapping(args.mapping))
        finally:
            engine.dispose()
    except SourceError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "import_batch_id",
                    "file_sha256",
                    "row_counts",
                    "canonical_counts",
                    "quarantined",
                    "quarantine_codes",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
