"""Deterministic Parquet storage and DuckDB view helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def read_parquet_or_empty(path: Path, columns: Sequence[str]) -> pd.DataFrame:
    """Read a Parquet table or return an empty frame with its declared columns."""
    if not path.exists():
        return pd.DataFrame(columns=list(columns))
    return pd.read_parquet(path)


def write_parquet(
    frame: pd.DataFrame,
    path: Path,
    *,
    sort_by: Sequence[str],
) -> None:
    """Sort and write a deterministic Parquet table without an index."""
    ordered = frame.sort_values(list(sort_by), kind="stable").reset_index(drop=True)
    table = pa.Table.from_pandas(ordered, preserve_index=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table,
        path,
        compression="zstd",
        use_dictionary=True,
        write_statistics=True,
        version="2.6",
    )


def upsert_frame(
    existing: pd.DataFrame,
    incoming: pd.DataFrame,
    *,
    keys: Sequence[str],
) -> pd.DataFrame:
    """Upsert incoming rows by key, with incoming values taking precedence."""
    if existing.empty:
        return incoming.copy()
    if incoming.empty:
        return existing.copy()
    combined = pd.concat([existing, incoming], ignore_index=True)
    return combined.drop_duplicates(subset=list(keys), keep="last").reset_index(
        drop=True
    )


def refresh_duckdb_views(data_dir: Path, table_names: Sequence[str]) -> None:
    """Expose existing Parquet tables as DuckDB views."""
    database_path = data_dir / "open_revisit.duckdb"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database_path)) as connection:
        for table_name in table_names:
            parquet_path = (data_dir / f"{table_name}.parquet").resolve()
            if not parquet_path.exists():
                continue
            escaped_path = str(parquet_path).replace("'", "''")
            connection.execute(
                f'CREATE OR REPLACE VIEW "{table_name}" AS '
                f"SELECT * FROM read_parquet('{escaped_path}')"
            )
