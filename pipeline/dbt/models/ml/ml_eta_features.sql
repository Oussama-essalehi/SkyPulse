-- ETA training set: one row per airborne position of a flight whose arrival airport
-- and arrival time were reconstructed. Target = seconds until arrival.

with flights as (
    select flight_id, arrival_airport, arrival_time, departure_time
    from {{ ref('fct_flights') }}
    where has_arrival
),

airports as (
    select airport_icao, any_value(latitude) as latitude, any_value(longitude) as longitude
    from {{ ref('stg_airports') }}
    group by airport_icao
),

positions as (
    select flight_id, icao24, position_ts, latitude, longitude, altitude_m, velocity_ms,
           vertical_rate_ms, track_deg
    from {{ ref('int_flight_segments') }}
    where not on_ground
),

winds as (
    select * from {{ ref('stg_upper_winds') }}
),

features as (
    select
        p.flight_id,
        p.icao24,
        p.position_ts,
        f.arrival_airport,
        f.arrival_time,
        date_diff('second', p.position_ts, f.arrival_time) as target_seconds,
        {{ haversine_km('p.latitude', 'p.longitude', 'a.latitude', 'a.longitude') }} as remaining_km,
        p.velocity_ms,
        p.altitude_m,
        coalesce(p.vertical_rate_ms, 0) as vertical_rate_ms,
        -- 1 = flying straight at the airport, -1 = flying away from it
        cos(radians(coalesce(p.track_deg, 0)
            - {{ bearing_deg('p.latitude', 'p.longitude', 'a.latitude', 'a.longitude') }}))
            as heading_alignment,
        hour(p.position_ts) as hour_utc,
        isodow(p.position_ts) as day_of_week,
        date_diff('second', f.departure_time, p.position_ts) / 60.0 as elapsed_min,
        -- Wind at cruise level along the track (NULL without weather data).
        {{ tailwind_ms('w.wind_speed_ms', 'w.wind_direction_deg', 'coalesce(p.track_deg, 0)') }}
            as tailwind_ms
    from positions as p
    inner join flights as f using (flight_id)
    inner join airports as a on a.airport_icao = f.arrival_airport
    left join winds as w
        on w.grid_lat = {{ wind_grid_lat('p.latitude') }}
        and w.grid_lon = {{ wind_grid_lon('p.longitude') }}
        and w.hour = date_trunc('hour', p.position_ts)
)

select *
from features
where target_seconds between 60 and 72000
  and velocity_ms > 30
  and remaining_km < 15000
