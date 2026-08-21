with observation_counts as (
  select
    configured.aoi_id,
    count(observations.datatake_id) filter (where observations.complete) as n_observations,
    count(observations.datatake_id) filter (
      where observations.complete and observations.usable
    ) as n_usable
  from {{ ref('_configured_aois') }} as configured
  left join {{ ref('_observations') }} as observations
    on configured.aoi_id = observations.aoi_id
  group by configured.aoi_id
),

gap_statistics as (
  select
    aoi_id,
    median(gap_days) filter (where kind = 'nominal') as nominal_median_gap_days,
    median(gap_days) filter (where kind = 'effective') as effective_median_gap_days,
    quantile_cont(gap_days, 0.9) filter (
      where kind = 'effective'
    ) as effective_p90_gap_days,
    max(gap_days) filter (where kind = 'effective') as longest_outage_days,
    count(*) filter (
      where kind = 'effective' and gap_days > 30
    ) as n_outages_over_30d
  from {{ ref('aoi_gaps') }}
  group by aoi_id
),

within_probabilities as (
  select
    aoi_id,
    max(1.0 - p_waiting) filter (where n_days = 3) as p_within_3d,
    max(1.0 - p_waiting) filter (where n_days = 5) as p_within_5d,
    max(1.0 - p_waiting) filter (where n_days = 7) as p_within_7d,
    max(1.0 - p_waiting) filter (where n_days = 14) as p_within_14d,
    max(1.0 - p_waiting) filter (where n_days = 30) as p_within_30d
  from {{ ref('aoi_survival') }}
  group by aoi_id
),

ranked_months as (
  select
    aoi_id,
    month,
    p_within_7d,
    row_number() over (
      partition by aoi_id order by p_within_7d desc, month asc
    ) as best_rank,
    row_number() over (
      partition by aoi_id order by p_within_7d asc, month asc
    ) as worst_rank
  from {{ ref('aoi_monthly') }}
  where n_days > 0
),

month_extremes as (
  select
    aoi_id,
    max(month) filter (where best_rank = 1) as best_month,
    max(month) filter (where worst_rank = 1) as worst_month,
    max(p_within_7d) filter (
      where best_rank = 1
    ) as p_within_7d_best_month,
    max(p_within_7d) filter (
      where worst_rank = 1
    ) as p_within_7d_worst_month
  from ranked_months
  group by aoi_id
),

catalog_t20 as (
  select
    aoi_id,
    precision as catalog_filter_precision_t20,
    recall as catalog_filter_recall_t20
  from {{ ref('catalog_filter_eval') }}
  where threshold = 20 and aoi_id != 'ALL'
)

select
  observations.aoi_id,
  '{{ var("config_hash") }}' as config_hash,
  cast(observations.n_observations as bigint) as n_observations,
  cast(observations.n_usable as bigint) as n_usable,
  {{ safe_ratio("observations.n_usable", "observations.n_observations") }} as usable_rate,
  coalesce(gaps.nominal_median_gap_days, 0.0) as nominal_median_gap_days,
  coalesce(gaps.effective_median_gap_days, 0.0) as effective_median_gap_days,
  coalesce(gaps.effective_p90_gap_days, 0.0) as effective_p90_gap_days,
  coalesce(gaps.longest_outage_days, 0.0) as longest_outage_days,
  cast(coalesce(gaps.n_outages_over_30d, 0) as bigint) as n_outages_over_30d,
  probabilities.p_within_3d,
  probabilities.p_within_5d,
  probabilities.p_within_7d,
  probabilities.p_within_14d,
  probabilities.p_within_30d,
  cast(coalesce(months.best_month, 0) as bigint) as best_month,
  cast(coalesce(months.worst_month, 0) as bigint) as worst_month,
  coalesce(months.p_within_7d_best_month, 0.0) as p_within_7d_best_month,
  coalesce(months.p_within_7d_worst_month, 0.0) as p_within_7d_worst_month,
  catalog.catalog_filter_precision_t20,
  catalog.catalog_filter_recall_t20
from observation_counts as observations
inner join within_probabilities as probabilities
  on observations.aoi_id = probabilities.aoi_id
inner join catalog_t20 as catalog
  on observations.aoi_id = catalog.aoi_id
left join gap_statistics as gaps
  on observations.aoi_id = gaps.aoi_id
left join month_extremes as months
  on observations.aoi_id = months.aoi_id
order by observations.aoi_id, config_hash
