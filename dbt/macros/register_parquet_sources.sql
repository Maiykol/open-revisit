{% macro register_parquet_sources() %}
  {% set observations_path = var('data_dir') ~ '/observations.parquet' %}
  {% set escaped_path = observations_path | replace("'", "''") %}
  {% do run_query('create schema if not exists parquet_sources') %}
  {% do run_query(
    "create or replace view parquet_sources.observations as "
    ~ "select * from read_parquet('" ~ escaped_path ~ "')"
  ) %}
{% endmacro %}
