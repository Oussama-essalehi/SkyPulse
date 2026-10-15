-- Airports usable as flight endpoints, one row per ICAO code (API, Superset maps).
select
    airport_icao,
    airport_iata,
    airport_name,
    airport_type,
    municipality,
    iso_country,
    latitude,
    longitude,
    elevation_m
from {{ ref('stg_airports') }}
qualify row_number() over (partition by airport_icao order by airport_type, airport_ident) = 1
