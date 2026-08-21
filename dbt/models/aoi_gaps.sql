with timelines as (
  select
    aoi_id,
    config_hash,
    datatake_id,
    observed_at,
    'nominal' as kind
  from {{ ref('_observations') }}
  where complete

  union all

  select
    aoi_id,
    config_hash,
    datatake_id,
    observed_at,
    'effective' as kind
  from {{ ref('_observations') }}
  where complete and usable
),

with_predecessor as (
  select
    aoi_id,
    config_hash,
    kind,
    lag(observed_at) over (
      partition by aoi_id, config_hash, kind
      order by observed_at, datatake_id
    ) as gap_start,
    observed_at as gap_end
  from timelines
)

select
  aoi_id,
  config_hash,
  kind,
  gap_start,
  gap_end,
  epoch(gap_end - gap_start) / 86400.0 as gap_days
from with_predecessor
where gap_start is not null
order by aoi_id, config_hash, kind, gap_start, gap_end
