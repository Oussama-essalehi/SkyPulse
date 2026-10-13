{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='partition_hour',
    )
}}

-- Typed, cleaned and de-duplicated state vectors.
-- Incremental: each run re-processes the last `staging_lookback_hours` hourly
-- partitions (late files, sink replays) and replaces them wholesale.

with raw as (
    {% if lake_has_files('aircraft_states') %}
    select * from {{ source('lake', 'aircraft_states') }}
    {% if is_incremental() %}
    where "date" >= (
        select cast(max(partition_hour) - to_hours({{ var('staging_lookback_hours') }}) as date)
        from {{ this }}
    )
    {% endif %}
    {% else %}
    -- Empty lake (first start): typed empty relation with the lake schema.
    select
        null::varchar as icao24, null::varchar as callsign, null::varchar as origin_country,
        null::bigint as time_position, null::bigint as last_contact, null::double as longitude,
        null::double as latitude, null::double as baro_altitude, null::boolean as on_ground,
        null::double as velocity, null::double as true_track, null::double as vertical_rate,
        null::double as geo_altitude, null::varchar as squawk, null::boolean as spi,
        null::integer as position_source, null::integer as category, null::bigint as snapshot_time,
        null::double as ingested_at, null::varchar as source, null::date as "date",
        null::bigint as "hour"
    where false
    {% endif %}
),

typed as (
    select
        lower(trim(icao24)) as icao24,
        nullif(trim(callsign), '') as callsign,
        origin_country,
        coalesce(time_position, last_contact) as position_epoch,
        make_timestamp(coalesce(time_position, last_contact) * 1000000) as position_ts,
        make_timestamp(snapshot_time * 1000000) as snapshot_ts,
        latitude,
        longitude,
        case when baro_altitude between -500 and 20000 then baro_altitude end as baro_altitude_m,
        case when geo_altitude between -500 and 20000 then geo_altitude end as geo_altitude_m,
        coalesce(on_ground, false) as on_ground,
        case when velocity between 0 and 400 then velocity end as velocity_ms,
        case when true_track between 0 and 360 then true_track end as track_deg,
        case when vertical_rate between -150 and 150 then vertical_rate end as vertical_rate_ms,
        nullif(trim(squawk), '') as squawk,
        category,
        source,
        make_timestamp(cast(ingested_at * 1000000 as bigint)) as ingested_ts,
        make_timestamp(cast(epoch("date") as bigint) * 1000000) + to_hours(cast("hour" as integer))
            as partition_hour
    from raw
    where latitude between -90 and 90
      and longitude between -180 and 180
      and icao24 is not null
)

select
    *,
    coalesce(baro_altitude_m, geo_altitude_m) as altitude_m
from typed
-- The sink is at-least-once: drop duplicate positions, keep the latest received.
qualify row_number() over (
    partition by icao24, position_epoch order by ingested_ts desc
) = 1
