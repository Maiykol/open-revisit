with horizons as (
  select
    configured.aoi_id,
    generated.n_days
  from {{ ref('_configured_aois') }} as configured
  cross join generate_series(0, {{ var("horizon_days") }}) as generated(n_days)
)

select
  horizons.aoi_id,
  '{{ var("config_hash") }}' as config_hash,
  cast(horizons.n_days as bigint) as n_days,
  {{ safe_ratio(
    "count(*) filter (where waits.wait_days > horizons.n_days)",
    "count(*)"
  ) }} as p_waiting
from horizons
inner join {{ ref('aoi_wait_daily') }} as waits
  on horizons.aoi_id = waits.aoi_id
group by horizons.aoi_id, horizons.n_days
order by horizons.aoi_id, config_hash, n_days
