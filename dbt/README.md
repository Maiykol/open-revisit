# dbt metric layer

This dbt-duckdb project reproduces the six Python metric tables directly from
the Parquet `observations` source. Run it from the repository root with:

```console
make dbt
```

The Make target resolves `config/dev.yaml`, passes its dates, AOIs, horizon,
config hash, and absolute `data_dir` as dbt variables, then runs `dbt build`.
The committed `profiles.yml` contains no credentials. Set
`OPEN_REVISIT_DBT_PATH` to override the generated DuckDB database path, or use
`python -m open_revisit.dbt_runner --config <path> --database-path <path>` for a
different local configuration.

Parquet remains the system of record. The DuckDB database, dbt targets, logs,
and packages are generated local state and are ignored by git.
