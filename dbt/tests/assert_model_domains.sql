with monthly_violations as (
  select aoi_id
  from {{ ref('aoi_monthly') }}
  group by aoi_id
  having count(*) != 12
    or count(distinct month) != 12
    or min(month) != 1
    or max(month) != 12
),

survival_violations as (
  select aoi_id
  from {{ ref('aoi_survival') }}
  group by aoi_id
  having count(*) != {{ var('horizon_days') }} + 1
    or count(distinct n_days) != {{ var('horizon_days') }} + 1
    or min(n_days) != 0
    or max(n_days) != {{ var('horizon_days') }}
),

catalog_violations as (
  select aoi_id
  from {{ ref('catalog_filter_eval') }}
  group by aoi_id
  having count(*) != 21
    or count(distinct threshold) != 21
    or min(threshold) != 0
    or max(threshold) != 100
    or count(*) filter (where threshold % 5 != 0) != 0
),

catalog_scope_violations as (
  select 'catalog_scope' as violation
  where (select count(distinct aoi_id) from {{ ref('catalog_filter_eval') }})
    != {{ var('aoi_ids') | length }} + 1
    or not exists (
      select 1 from {{ ref('catalog_filter_eval') }} where aoi_id = 'ALL'
    )
)

select 'monthly:' || aoi_id as violation from monthly_violations
union all
select 'survival:' || aoi_id as violation from survival_violations
union all
select 'catalog:' || aoi_id as violation from catalog_violations
union all
select violation from catalog_scope_violations
