{# Helpers for the reference files downloaded by `make seed` (data/reference). #}

{% macro reference_path(name) -%}
    {{ env_var('SKYRADAR_REFERENCE_ROOT', '/data/reference') }}/{{ name }}
{%- endmacro %}

{#
  True when the reference file exists. The aircraft database is optional
  (`make seed` can skip it): models then build from a typed empty relation and
  the dimensions are simply empty.
#}
{% macro reference_has_file(name) %}
    {% if not execute %}
        {{ return(true) }}
    {% endif %}
    {% set result = run_query("select count(*) from glob('" ~ reference_path(name) ~ "')") %}
    {{ return(result.columns[0].values()[0] > 0) }}
{% endmacro %}

{# ICAO airline designator from an airline callsign (3 letters + flight number). #}
{% macro airline_icao(callsign) -%}
    case when regexp_matches({{ callsign }}, '^[A-Z]{3}[0-9]') then left({{ callsign }}, 3) end
{%- endmacro %}
