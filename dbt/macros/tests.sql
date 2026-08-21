{% test unique_combination_of_columns(model, combination_of_columns) %}
select
  {% for column_name in combination_of_columns %}
  {{ column_name }}{% if not loop.last %}, {% endif %}
  {% endfor %}
from {{ model }}
group by
  {% for column_name in combination_of_columns %}
  {{ column_name }}{% if not loop.last %}, {% endif %}
  {% endfor %}
having count(*) > 1
{% endtest %}

{% test accepted_range(model, column_name, min_value=none, max_value=none) %}
select *
from {{ model }}
where
  {% if min_value is not none %}{{ column_name }} < {{ min_value }}{% endif %}
  {% if min_value is not none and max_value is not none %}or{% endif %}
  {% if max_value is not none %}{{ column_name }} > {{ max_value }}{% endif %}
{% endtest %}

{% macro safe_ratio(numerator, denominator) %}
coalesce(cast({{ numerator }} as double) / nullif({{ denominator }}, 0), 0.0)
{% endmacro %}
