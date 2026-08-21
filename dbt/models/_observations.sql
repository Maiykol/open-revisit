{{ config(materialized='ephemeral') }}

select
  observations.aoi_id,
  observations.datatake_id,
  observations.config_hash,
  observations.observed_at,
  observations.catalog_cloud_cover,
  observations.usable,
  observations.complete
from {{ source('parquet', 'observations') }} as observations
inner join {{ ref('_configured_aois') }} as configured
  on observations.aoi_id = configured.aoi_id
where observations.config_hash = '{{ var("config_hash") }}'
