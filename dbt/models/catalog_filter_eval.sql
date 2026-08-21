with thresholds as (
  select generated.threshold
  from generate_series(0, 100, 5) as generated(threshold)
),

scopes as (
  select aoi_id
  from {{ ref('_configured_aois') }}

  union all

  select 'ALL' as aoi_id
),

complete_observations as (
  select
    aoi_id,
    catalog_cloud_cover,
    usable
  from {{ ref('_observations') }}
  where complete
),

scoped_observations as (
  select
    aoi_id as metric_aoi_id,
    catalog_cloud_cover,
    usable
  from complete_observations

  union all

  select
    'ALL' as metric_aoi_id,
    catalog_cloud_cover,
    usable
  from complete_observations
),

confusion as (
  select
    scopes.aoi_id,
    thresholds.threshold,
    count(scoped.metric_aoi_id) filter (
      where scoped.catalog_cloud_cover <= thresholds.threshold and scoped.usable
    ) as tp,
    count(scoped.metric_aoi_id) filter (
      where scoped.catalog_cloud_cover <= thresholds.threshold and not scoped.usable
    ) as fp,
    count(scoped.metric_aoi_id) filter (
      where (scoped.catalog_cloud_cover > thresholds.threshold
        or scoped.catalog_cloud_cover is null) and scoped.usable
    ) as fn,
    count(scoped.metric_aoi_id) filter (
      where (scoped.catalog_cloud_cover > thresholds.threshold
        or scoped.catalog_cloud_cover is null) and not scoped.usable
    ) as tn
  from scopes
  cross join thresholds
  left join scoped_observations as scoped
    on scopes.aoi_id = scoped.metric_aoi_id
  group by scopes.aoi_id, thresholds.threshold
)

select
  aoi_id,
  '{{ var("config_hash") }}' as config_hash,
  cast(threshold as bigint) as threshold,
  cast(tp as bigint) as tp,
  cast(fp as bigint) as fp,
  cast(fn as bigint) as fn,
  cast(tn as bigint) as tn,
  {{ safe_ratio("tp", "tp + fp") }} as precision,
  {{ safe_ratio("tp", "tp + fn") }} as recall,
  {{ safe_ratio("2 * tp", "2 * tp + fp + fn") }} as f1,
  {{ safe_ratio("fp", "tp + fp") }} as kept_unusable_rate,
  {{ safe_ratio("fn", "tp + fn") }} as discarded_usable_rate
from confusion
order by aoi_id, config_hash, threshold
