import csv
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from open_revisit import __version__
from open_revisit.cli import app

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "stac" / "berlin_items.json"


def test_version_option() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def _write_centroid_csv(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["aoi_id", "name", "country", "lat", "lon"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "aoi_id": "berlin",
                "name": "Berlin",
                "country": "DE",
                "lat": "52.52437",
                "lon": "13.41053",
            }
        )


def test_aois_build_command(tmp_path: Path) -> None:
    csv_path = tmp_path / "centroids.csv"
    output_dir = tmp_path / "aois"
    parquet_path = tmp_path / "data" / "aois.parquet"
    _write_centroid_csv(csv_path)

    result = CliRunner().invoke(
        app,
        [
            "aois",
            "build",
            str(csv_path),
            "--output-dir",
            str(output_dir),
            "--parquet-path",
            str(parquet_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "aoi.build" in result.output
    assert (output_dir / "berlin.geojson").exists()
    assert parquet_path.exists()


def test_discover_command_is_idempotent_with_fixture(tmp_path: Path) -> None:
    csv_path = tmp_path / "centroids.csv"
    aoi_dir = tmp_path / "aois"
    data_dir = tmp_path / "data"
    config_path = tmp_path / "test.yaml"
    _write_centroid_csv(csv_path)
    build_result = CliRunner().invoke(
        app,
        [
            "aois",
            "build",
            str(csv_path),
            "--output-dir",
            str(aoi_dir),
            "--parquet-path",
            str(data_dir / "aois.parquet"),
        ],
    )
    assert build_result.exit_code == 0, build_result.output
    config_path.write_text(
        "\n".join(
            [
                "stac_url: https://earth-search.aws.element84.com/v1",
                "collection: sentinel-2-l2a",
                "start: 2025-06-26",
                "end: 2025-06-27",
                "aois: [berlin]",
                f"data_dir: {data_dir}",
                f"stac_fixture: {FIXTURE_PATH}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    first = CliRunner().invoke(app, ["discover", "--config", str(config_path)])
    second = CliRunner().invoke(app, ["discover", "--config", str(config_path)])

    assert first.exit_code == 0, first.output
    assert "new=2" in first.output
    assert "superseded=1" in first.output
    assert second.exit_code == 0, second.output
    assert "new=0" in second.output
    assert "superseded=0" in second.output
    assert len(pd.read_parquet(data_dir / "scenes.parquet")) == 2
    assert len(pd.read_parquet(data_dir / "scene_aoi.parquet")) == 2
    assert len(pd.read_parquet(data_dir / "scenes_superseded.parquet")) == 1
    assert len(pd.read_parquet(data_dir / "ingest_state.parquet")) == 1
