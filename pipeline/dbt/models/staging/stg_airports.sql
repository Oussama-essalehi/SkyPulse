-- Airports usable as flight endpoints (OurAirports).
select
    ident as airport_ident,
    coalesce(nullif(icao_code, ''), nullif(gps_code, ''), ident) as airport_icao,
    nullif(iata_code, '') as airport_iata,
    name as airport_name,
    type as airport_type,
    cast(latitude_deg as double) as latitude,
    cast(longitude_deg as double) as longitude,
    cast(elevation_ft as double) * 0.3048 as elevation_m,
    iso_country,
    municipality,
    scheduled_service = 'yes' as has_scheduled_service
from {{ source('reference', 'airports') }}
where type in ('large_airport', 'medium_airport')
   or (type = 'small_airport' and scheduled_service = 'yes')
