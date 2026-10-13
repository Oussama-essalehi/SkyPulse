-- Trajectory features per reconstructed flight, input of the Isolation Forest.

with airborne as (
    select
        *,
        lag(track_deg) over w as prev_track,
        lag(position_epoch) over w as prev_epoch
    from {{ ref('int_flight_segments') }}
    where not on_ground
    window w as (partition by flight_id order by position_epoch)
),

steps as (
    select
        *,
        abs(((track_deg - prev_track + 540) % 360) - 180) as turn_deg,
        position_epoch - prev_epoch as dt_s
    from airborne
),

per_flight as (
    select
        flight_id,
        any_value(icao24) as icao24,
        mode(callsign) filter (where callsign is not null) as callsign,
        min(position_ts) as first_seen_ts,
        max(position_ts) as last_seen_ts,
        count(*) as n_positions,
        (max(position_epoch) - min(position_epoch)) / 60.0 as duration_min,
        sum(step_km) as path_km,
        arg_min(latitude, position_epoch) as first_latitude,
        arg_min(longitude, position_epoch) as first_longitude,
        arg_max(latitude, position_epoch) as last_latitude,
        arg_max(longitude, position_epoch) as last_longitude,
        max(altitude_m) as max_altitude_m,
        coalesce(stddev_pop(altitude_m), 0) as altitude_std_m,
        coalesce(max(abs(vertical_rate_ms)), 0) as max_abs_vertical_rate_ms,
        avg(velocity_ms) as mean_speed_ms,
        coalesce(stddev_pop(velocity_ms), 0) as speed_std_ms,
        -- turn rate only between close samples (heading deltas alias otherwise)
        coalesce(
            sum(turn_deg) filter (where dt_s <= 120)
            / nullif(sum(dt_s) filter (where dt_s <= 120), 0) * 60, 0
        ) as turn_rate_deg_per_min,
        count(*) filter (where dt_s > 300) as n_gaps,
        bool_or(squawk in ('7500', '7600', '7700')) as had_emergency_squawk
    from steps
    group by flight_id
    having count(*) >= 5
)

select
    * exclude (first_latitude, first_longitude),
    {{ haversine_km('first_latitude', 'first_longitude', 'last_latitude', 'last_longitude') }}
        as displacement_km,
    least(1.0, {{ haversine_km('first_latitude', 'first_longitude', 'last_latitude', 'last_longitude') }}
        / nullif(path_km, 0)) as straightness
from per_flight
