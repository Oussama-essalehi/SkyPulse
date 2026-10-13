-- Altitude distribution of airborne positions (1000 m bands) per day.
select
    cast(position_ts as date) as day,
    cast(floor(altitude_m / 1000) * 1000 as integer) as altitude_band_m,
    count(*) as positions,
    count(distinct icao24) as aircraft
from {{ ref('stg_aircraft_states') }}
where not on_ground
  and altitude_m is not null
  and altitude_m >= 0
group by all
