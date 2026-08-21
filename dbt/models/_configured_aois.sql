{{ config(materialized='ephemeral') }}

select configured.aoi_id
from (
  values
  {% for aoi_id in var('aoi_ids') %}
    ('{{ aoi_id | replace("'", "''") }}'){% if not loop.last %},{% endif %}
  {% endfor %}
) as configured(aoi_id)
