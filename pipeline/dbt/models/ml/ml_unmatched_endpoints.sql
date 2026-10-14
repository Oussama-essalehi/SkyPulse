-- Take-offs and landings (low endpoints) with no known airport within
-- `endpoint_max_distance_km`: input of the DBSCAN clustering (skyradar_ml.endpoints),
-- which finds the airfields, heliports and coverage artefacts behind them.
with flights as (
    select * from {{ ref('int_flight_endpoints') }}
    where n_airborne_positions >= 3
)

select
    flight_id,
    icao24,
    callsign,
    'departure' as endpoint,
    first_latitude as latitude,
    first_longitude as longitude,
    first_seen_ts as endpoint_ts
from flights
where departure_is_low and departure_airport is null
union all
select
    flight_id,
    icao24,
    callsign,
    'arrival' as endpoint,
    last_latitude as latitude,
    last_longitude as longitude,
    last_seen_ts as endpoint_ts
from flights
where arrival_is_low and arrival_airport is null
