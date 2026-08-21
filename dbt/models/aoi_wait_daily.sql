with start_days as (
  select
    configured.aoi_id,
    cast(generated.t0 as timestamp with time zone) as t0
  from {{ ref('_configured_aois') }} as configured
  cross join generate_series(
    cast('{{ var("start") }}' as date),
    cast('{{ var("end") }}' as date) - interval '{{ var("horizon_days") }} days',
    interval '1 day'
  ) as generated(t0)
),

next_usable as (
  select
    start_days.aoi_id,
    start_days.t0,
    min(observations.observed_at) as next_observed_at
  from start_days
  left join {{ ref('_observations') }} as observations
    on start_days.aoi_id = observations.aoi_id
    and observations.complete
    and observations.usable
    and observations.observed_at >= start_days.t0
    and observations.observed_at
      <= start_days.t0 + interval '{{ var("horizon_days") }} days'
  group by start_days.aoi_id, start_days.t0
)

select
  aoi_id,
  '{{ var("config_hash") }}' as config_hash,
  t0,
  case
    when next_observed_at is null then cast({{ var("horizon_days") }} as double)
    else epoch(next_observed_at - t0) / 86400.0
  end as wait_days,
  next_observed_at is null as censored
from next_usable
order by aoi_id, config_hash, t0
