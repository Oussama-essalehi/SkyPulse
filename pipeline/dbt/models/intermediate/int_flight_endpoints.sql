-- One row per reconstructed flight with its first / last fix and the nearest
-- airport at each end (only when that end is on the ground or low enough).

with segments as (
    select * from {{ ref('int_flight_segments') }}
),

summary as (
    select
        flight_id,
        icao24,
        mode(callsign) filter (where callsign is not null) as callsign,
        any_value(origin_country) as origin_country,
        min(position_ts) as first_seen_ts,
        max(position_ts) as last_seen_ts,
        min(position_ts) filter (where not on_ground) as first_airborne_ts,
        max(position_ts) filter (where not on_ground) as last_airborne_ts,
        arg_min(latitude, position_epoch) as first_latitude,
        arg_min(longitude, position_epoch) as first_longitude,
        arg_min(altitude_m, position_epoch) as first_altitude_m,
        arg_min(on_ground, position_epoch) as first_on_ground,
        arg_max(latitude, position_epoch) as last_latitude,
        arg_max(longitude, position_epoch) as last_longitude,
        arg_max(altitude_m, position_epoch) as last_altitude_m,
        arg_max(on_ground, position_epoch) as last_on_ground,
        count(*) as n_positions,
        count(*) filter (where not on_ground) as n_airborne_positions,
        max(altitude_m) as max_altitude_m,
        sum(step_km) as distance_km,
        bool_or(squawk in ('7500', '7600', '7700')) as had_emergency_squawk
    from segments
    group by flight_id, icao24
),

endpoints as (
    select flight_id, 'departure' as endpoint, first_latitude as latitude,
           first_longitude as longitude,
           (first_on_ground or coalesce(first_altitude_m, 0) <= {{ var('endpoint_max_altitude_m') }})
               as is_low
    from summary
    union all
    select flight_id, 'arrival', last_latitude, last_longitude,
           (last_on_ground or coalesce(last_altitude_m, 0) <= {{ var('endpoint_max_altitude_m') }})
    from summary
),

-- Grid join (0.5 degree cells + neighbours) instead of a full cross join.
airport_cells as (
    select
        a.*,
        cast(floor(a.latitude * 2) as integer) + dy as cell_y,
        cast(floor(a.longitude * 2) as integer) + dx as cell_x
    from {{ ref('stg_airports') }} as a
    cross join (values (-1), (0), (1)) as y(dy)
    cross join (values (-1), (0), (1)) as x(dx)
),

candidates as (
    select
        e.flight_id,
        e.endpoint,
        a.airport_icao,
        a.airport_name,
        a.iso_country as airport_country,
        {{ haversine_km('e.latitude', 'e.longitude', 'a.latitude', 'a.longitude') }} as distance_km
    from endpoints as e
    inner join airport_cells as a
        on cast(floor(e.latitude * 2) as integer) = a.cell_y
        and cast(floor(e.longitude * 2) as integer) = a.cell_x
    where e.is_low
),

nearest as (
    select *
    from candidates
    where distance_km <= {{ var('endpoint_max_distance_km') }}
    qualify row_number() over (partition by flight_id, endpoint order by distance_km) = 1
)

select
    s.*,
    dep.airport_icao as departure_airport,
    dep.airport_name as departure_airport_name,
    dep.distance_km as departure_match_km,
    arr.airport_icao as arrival_airport,
    arr.airport_name as arrival_airport_name,
    arr.distance_km as arrival_match_km
from summary as s
left join nearest as dep on dep.flight_id = s.flight_id and dep.endpoint = 'departure'
left join nearest as arr on arr.flight_id = s.flight_id and arr.endpoint = 'arrival'
