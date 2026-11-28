import argparse
import json
from pathlib import Path

from erp_agent.config import Settings
from erp_agent.database.engine import database_engine
from erp_agent.ingestion.scenario_builder import (
    SCENARIO_PATH,
    build_scenarios,
    read_scenario,
    register_fixture,
)
from erp_agent.ingestion.source_manifest import SourceError
from scripts.seed_demo import seed


def main() -> None:
    config = Settings()
    parser = argparse.ArgumentParser(description="Build immutable FinanceBenchmark demo scenarios")
    parser.add_argument("--config", type=Path, default=SCENARIO_PATH)
    parser.add_argument("--fixture-only", action="store_true")
    args = parser.parse_args()
    if config.app_env != "development":
        raise SystemExit("SCENARIO_BUILD_DISABLED: development environment required")
    db = database_engine(config.import_url)
    try:
        versions = {}
        if not args.fixture_only:
            scenario = read_scenario(args.config)
            if args.config == SCENARIO_PATH:
                scenario = type(scenario).model_validate(
                    {
                        **scenario.model_dump(),
                        "as_of_date": config.demo_as_of_date,
                        "scenario_id": config.demo_scenario,
                        "seed": config.demo_scenario_seed,
                    }
                )
            versions = build_scenarios(db, scenario)
        seed(db, finance=True)
        versions["fixture_only"] = register_fixture(db)
    except SourceError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        db.dispose()
    print(json.dumps(versions))


if __name__ == "__main__":
    main()
