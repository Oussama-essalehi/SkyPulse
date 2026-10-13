-- Most frequent departure -> arrival pairs.
with airports as (
    select airport_icao, any_value(latitude) as latitude, any_value(longitude) as longitude
    from {{ ref('stg_airports') }}
    group by airport_icao
)

select
    f.departure_airport || '-' || f.arrival_airport as route,
    f.departure_airport,
    any_value(f.departure_airport_name) as departure_airport_name,
    f.arrival_airport,
    any_value(f.arrival_airport_name) as arrival_airport_name,
    count(*) as flights,
    count(distinct f.callsign) as distinct_callsigns,
    round(avg(f.duration_min), 1) as avg_duration_min,
    round(avg(f.distance_km), 1) as avg_distance_km,
    min(f.departure_time) as first_seen,
    max(f.departure_time) as last_seen,
    any_value(dep.latitude) as departure_latitude,
    any_value(dep.longitude) as departure_longitude,
    any_value(arr.latitude) as arrival_latitude,
    any_value(arr.longitude) as arrival_longitude
from {{ ref('fct_flights') }} as f
left join airports as dep on dep.airport_icao = f.departure_airport
left join airports as arr on arr.airport_icao = f.arrival_airport
where f.is_complete and f.departure_airport <> f.arrival_airport
group by f.departure_airport, f.arrival_airport
