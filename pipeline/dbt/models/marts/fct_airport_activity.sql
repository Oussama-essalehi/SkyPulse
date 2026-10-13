-- Departures / arrivals per airport and hour (reconstructed flights).
with movements as (
    select departure_airport as airport_icao, date_trunc('hour', departure_time) as hour,
           1 as departures, 0 as arrivals
    from {{ ref('fct_flights') }}
    where departure_airport is not null
    union all
    select arrival_airport, date_trunc('hour', arrival_time), 0, 1
    from {{ ref('fct_flights') }}
    where arrival_airport is not null
),

airports as (
    -- One row per ICAO code (a few OurAirports idents share a gps code).
    select *
    from {{ ref('stg_airports') }}
    qualify row_number() over (partition by airport_icao order by airport_type, airport_ident) = 1
)

select
    m.airport_icao,
    a.airport_name,
    a.airport_iata,
    a.iso_country,
    a.latitude,
    a.longitude,
    m.hour,
    sum(m.departures) as departures,
    sum(m.arrivals) as arrivals,
    sum(m.departures + m.arrivals) as movements
from movements as m
left join airports as a using (airport_icao)
group by all
