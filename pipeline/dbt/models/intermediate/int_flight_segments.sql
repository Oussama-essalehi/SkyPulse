-- Positions split into flights, per aircraft:
-- a new flight starts after a time gap > flight_gap_seconds, or at take-off
-- (previous position on the ground, current one airborne).

with positions as (
    select
        icao24, callsign, origin_country, position_epoch, position_ts, latitude, longitude,
        altitude_m, on_ground, velocity_ms, track_deg, vertical_rate_ms, squawk
    from {{ ref('stg_aircraft_states') }}
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
