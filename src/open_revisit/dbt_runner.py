"""Repository-local dbt orchestration for the Parquet metric layer."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from open_revisit.config import AppConfig, load_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DBT_PROJECT = PROJECT_ROOT / "dbt"


def dbt_variables(config: AppConfig) -> dict[str, Any]:
    """Return the dbt variables that define one metric calculation."""
    return {
        "data_dir": str(config.data_dir.resolve()),
        "config_hash": config.config_hash(),
        "start": config.start.isoformat(),
        "end": config.end.isoformat(),
        "horizon_days": config.horizon_days,
        "aoi_ids": list(config.aoi_ids),
    }


def run_dbt_build(
    config: AppConfig,
    *,
    project_dir: Path = DEFAULT_DBT_PROJECT,
    database_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run dbt build, including all model and data tests, for one config."""
    observations_path = config.data_dir / "observations.parquet"
    if not observations_path.exists():
        raise FileNotFoundError(
            f"observation table not found: {observations_path}; run process first"
        )
    resolved_project = project_dir.resolve()
    resolved_database = (
        database_path or config.data_dir / "dbt" / "open_revisit.duckdb"
    ).resolve()
    resolved_database.parent.mkdir(parents=True, exist_ok=True)
    dbt_executable = Path(sys.executable).with_name("dbt")
    environment = os.environ.copy()
    environment["OPEN_REVISIT_DBT_PATH"] = str(resolved_database)
    command = [
        str(dbt_executable),
        "build",
        "--project-dir",
        str(resolved_project),
        "--profiles-dir",
        str(resolved_project),
        "--vars",
        json.dumps(dbt_variables(config), sort_keys=True),
    ]
    return subprocess.run(command, check=True, env=environment, text=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the open-revisit dbt layer")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project-dir", type=Path, default=DEFAULT_DBT_PROJECT)
    parser.add_argument("--database-path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Load configuration and run the repository-local dbt build."""
    arguments = _parser().parse_args(argv)
    config = load_config(arguments.config)
    run_dbt_build(
        config,
        project_dir=arguments.project_dir,
        database_path=arguments.database_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
