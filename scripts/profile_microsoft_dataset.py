import argparse
from pathlib import Path

import yaml

from erp_agent.ingestion.profile import profile, write_profile
from erp_agent.ingestion.source_manifest import SOURCE_PATH, SourceError
from erp_agent.ingestion.source_reader import load_source


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile the verified Microsoft CSV/XLSX archive")
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--output", type=Path, default=Path("data/profiling"))
    parser.add_argument(
        "--mapping", type=Path, default=Path("data/mappings/financebenchmark-v1.yaml")
    )
    parser.add_argument("--publish-doc", type=Path)
    args = parser.parse_args()
    try:
        identity, sheets = load_source(args.source)
        manifest, columns = profile(identity, sheets)
        semantics = (
            yaml.safe_load(args.mapping.read_text(encoding="utf-8"))["semantics"]
            if args.mapping.is_file()
            else None
        )
        report = write_profile(args.output, manifest, columns, semantics)
        if args.publish_doc:
            args.publish_doc.write_text(report, encoding="utf-8")
    except SourceError as exc:
        raise SystemExit(str(exc)) from exc
    print(manifest["row_counts"], manifest["file_sha256"])


if __name__ == "__main__":
    main()
