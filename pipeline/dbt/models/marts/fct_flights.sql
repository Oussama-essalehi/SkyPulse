-- One reconstructed flight per row (needs at least 3 airborne positions).
select
    flight_id,
    icao24,
    callsign,
    origin_country,
    departure_airport,
    departure_airport_name,
    arrival_airport,
    arrival_airport_name,
    coalesce(first_airborne_ts, first_seen_ts) as departure_time,
    coalesce(last_airborne_ts, last_seen_ts) as arrival_time,
    round(
        date_diff('second', coalesce(first_airborne_ts, first_seen_ts),
                  coalesce(last_airborne_ts, last_seen_ts)) / 60.0, 1
    ) as duration_min,
    round(distance_km, 1) as distance_km,
    round(max_altitude_m) as max_altitude_m,
    n_positions,
    departure_airport is not null as has_departure,
    arrival_airport is not null as has_arrival,
    departure_airport is not null and arrival_airport is not null as is_complete,
    had_emergency_squawk
from {{ ref('int_flight_endpoints') }}
where n_airborne_positions >= 3
