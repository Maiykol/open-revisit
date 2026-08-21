with expected_waits as (
  select date_diff(
    'day',
    cast('{{ var("start") }}' as date),
    cast('{{ var("end") }}' as date) - interval '{{ var("horizon_days") }} days'
  ) + 1 as n_rows
),

wait_violations as (
  select aoi_id
  from {{ ref('aoi_wait_daily') }}
  group by aoi_id
  having count(*) != (select n_rows from expected_waits)
),

config_hashes as (
  select config_hash from {{ ref('aoi_gaps') }}
  union all select config_hash from {{ ref('aoi_wait_daily') }}
  union all select config_hash from {{ ref('aoi_survival') }}
  union all select config_hash from {{ ref('aoi_monthly') }}
  union all select config_hash from {{ ref('aoi_summary') }}
  union all select config_hash from {{ ref('catalog_filter_eval') }}
),

config_violations as (
  select distinct config_hash
  from config_hashes
  where config_hash != '{{ var("config_hash") }}'
)

select 'wait:' || aoi_id as violation from wait_violations
union all
select 'config:' || config_hash as violation from config_violations
