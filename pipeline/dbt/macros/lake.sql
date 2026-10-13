{# Helpers to read the raw Parquet lake written by the streaming sink. #}

{% macro lake_glob(dataset) -%}
    {{ env_var('SKYRADAR_LAKE_ROOT', 's3://skyradar-lake') }}/raw/{{ dataset }}/*/*/*.parquet
{%- endmacro %}

{#
  True when the dataset already has files. The lake is empty right after the
  first `make up`; models then build from a typed empty relation instead of
  failing, so `dbt build` is green from minute one.
#}
{% macro lake_has_files(dataset) %}
    {% if not execute %}
        {{ return(true) }}
    {% endif %}
    {% set result = run_query("select count(*) from glob('" ~ lake_glob(dataset) ~ "')") %}
    {{ return(result.columns[0].values()[0] > 0) }}
{% endmacro %}
