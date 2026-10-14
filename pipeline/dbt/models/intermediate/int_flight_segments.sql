-- Positions split into flights, per aircraft:
-- a new flight starts after a time gap > flight_gap_seconds, at take-off (previous
-- position on the ground, current one airborne), or after a physically impossible jump.
-- Isolated position glitches ("spikes": impossible speed both from the previous and to
-- the next fix) are removed first.

with raw_positions as (
    select
        icao24, callsign, origin_country, position_epoch, position_ts, latitude, longitude,
        altitude_m, on_ground, velocity_ms, track_deg, vertical_rate_ms, squawk
    from {{ ref('stg_aircraft_states') }}
),

implied_speeds as (
    select
        *,
        {{ haversine_km('lag(latitude) over w', 'lag(longitude) over w', 'latitude', 'longitude') }}
            * 1000 / nullif(position_epoch - lag(position_epoch) over w, 0) as speed_from_prev_ms,
        {{ haversine_km('latitude', 'longitude', 'lead(latitude) over w', 'lead(longitude) over w') }}
            * 1000 / nullif(lead(position_epoch) over w - position_epoch, 0) as speed_to_next_ms
    from raw_positions
    window w as (partition by icao24 order by position_epoch)
),

positions as (
    select * exclude (speed_from_prev_ms, speed_to_next_ms)
    from implied_speeds
    where not (
        coalesce(speed_from_prev_ms, 0) > {{ var('max_implied_speed_ms') }}
        and coalesce(speed_to_next_ms, 0) > {{ var('max_implied_speed_ms') }}
    )
),

with_previous as (
    select
        *,
        lag(position_epoch) over w as prev_epoch,
        lag(on_ground) over w as prev_on_ground,
        lag(latitude) over w as prev_latitude,
        lag(longitude) over w as prev_longitude
    from positions
    window w as (partition by icao24 order by position_epoch)
),

flagged as (
    select
        *,
        (
            prev_epoch is null
            or position_epoch - prev_epoch > {{ var('flight_gap_seconds') }}
            or (prev_on_ground and not on_ground)
            or {{ haversine_km('prev_latitude', 'prev_longitude', 'latitude', 'longitude') }}
                * 1000 / nullif(position_epoch - prev_epoch, 0) > {{ var('max_implied_speed_ms') }}
        ) as starts_flight
    from with_previous
),

segmented as (
    select
        *,
        sum(case when starts_flight then 1 else 0 end) over (
            partition by icao24 order by position_epoch
            rows between unbounded preceding and current row
        ) as segment_seq
    from flagged
)

select
    icao24 || '-' || cast(
        min(position_epoch) over (partition by icao24, segment_seq) as varchar
    ) as flight_id,
    icao24,
    segment_seq,
    callsign,
    origin_country,
    position_epoch,
    position_ts,
    latitude,
    longitude,
    altitude_m,
    on_ground,
    velocity_ms,
    track_deg,
    vertical_rate_ms,
    squawk,
    starts_flight,
    case
        when starts_flight then 0.0
        else {{ haversine_km('prev_latitude', 'prev_longitude', 'latitude', 'longitude') }}
    end as step_km
from segmented
