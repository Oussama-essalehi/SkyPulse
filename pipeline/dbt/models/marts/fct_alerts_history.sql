-- One row per alert (lifecycle events collapsed): when it started, last update, resolution.

with events as (
    {% if lake_has_files('alerts') %}
    select * from {{ source('lake', 'alerts') }}
    {% else %}
    select
        null::varchar as alert_id, null::varchar as icao24, null::varchar as callsign,
        null::varchar as type, null::varchar as severity, null::varchar as status,
        null::varchar as message, null::double as latitude, null::double as longitude,
        null::double as altitude, null::bigint as event_time, null::double as detected_at,
        null::varchar as details_json
    where false
    {% endif %}
)

select
    alert_id,
    any_value(icao24) as icao24,
    arg_max(callsign, detected_at) as callsign,
    any_value(type) as alert_type,
    any_value(severity) as severity,
    arg_min(message, detected_at) as message,
    make_timestamp(min(event_time) * 1000000) as started_at,
    make_timestamp(cast(max(detected_at) * 1000000 as bigint)) as last_update_at,
    bool_or(status = 'resolved') as is_resolved,
    round(
        (max(detected_at) filter (where status = 'resolved') - min(event_time)) / 60.0, 1
    ) as duration_min,
    arg_min(latitude, detected_at) as latitude,
    arg_min(longitude, detected_at) as longitude,
    arg_min(altitude, detected_at) as altitude_m,
    count(*) as events
from events
group by alert_id
