{#
    Post-hook keeping `days` of history in an incremental table, counted back from its
    newest row (not from now(): a replayed or paused lake must not be wiped out).
    days <= 0 keeps everything.
#}
{% macro history_retention_hook(column, days) %}
    {%- if days | int > 0 -%}
    delete from {{ this }}
    where {{ column }} < (select max({{ column }}) from {{ this }}) - to_days({{ days | int }})
    {%- else -%}
    select 1
    {%- endif -%}
{% endmacro %}
