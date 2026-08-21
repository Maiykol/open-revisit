with months as (
  select
    configured.aoi_id,
    generated.month
  from {{ ref('_configured_aois') }} as configured
  cross join generate_series(1, 12) as generated(month)
),

monthly_counts as (
  select
    months.aoi_id,
    months.month,
    count(waits.t0) as n_days,
    count(waits.t0) filter (where waits.wait_days <= 5) as n_within_5d,
    count(waits.t0) filter (where waits.wait_days <= 7) as n_within_7d,
    count(waits.t0) filter (where waits.wait_days <= 14) as n_within_14d
  from months
  left join {{ ref('aoi_wait_daily') }} as waits
    on months.aoi_id = waits.aoi_id
    and extract(month from waits.t0) = months.month
  group by months.aoi_id, months.month
)

select
  aoi_id,
  '{{ var("config_hash") }}' as config_hash,
  cast(month as bigint) as month,
  {{ safe_ratio("n_within_5d", "n_days") }} as p_within_5d,
  {{ safe_ratio("n_within_7d", "n_days") }} as p_within_7d,
  {{ safe_ratio("n_within_14d", "n_days") }} as p_within_14d,
  cast(n_days as bigint) as n_days
from monthly_counts
order by aoi_id, config_hash, month
