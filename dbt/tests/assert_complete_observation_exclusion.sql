with expected as (
  select
    configured.aoi_id,
    count(observations.datatake_id) filter (where observations.complete) as n_complete
  from {{ ref('_configured_aois') }} as configured
  left join {{ ref('_observations') }} as observations
    on configured.aoi_id = observations.aoi_id
  group by configured.aoi_id
),

summary_violations as (
  select summary.aoi_id
  from {{ ref('aoi_summary') }} as summary
  inner join expected on summary.aoi_id = expected.aoi_id
  where summary.n_observations != expected.n_complete
),

catalog_totals as (
  select
    catalog.aoi_id,
    catalog.threshold,
    catalog.tp + catalog.fp + catalog.fn + catalog.tn as n_evaluated,
    case
      when catalog.aoi_id = 'ALL' then (select sum(n_complete) from expected)
      else expected.n_complete
    end as n_expected
  from {{ ref('catalog_filter_eval') }} as catalog
  left join expected on catalog.aoi_id = expected.aoi_id
),

catalog_violations as (
  select aoi_id, threshold
  from catalog_totals
  where n_evaluated != n_expected
)

select 'summary:' || aoi_id as violation from summary_violations
union all
select 'catalog:' || aoi_id || ':' || threshold as violation
from catalog_violations
