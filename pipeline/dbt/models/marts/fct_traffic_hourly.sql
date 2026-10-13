-- Distinct aircraft per hour, geographic zone and country of registration.
select
    date_trunc('hour', position_ts) as hour,
    {{ geo_zone('latitude', 'longitude') }} as zone,
    origin_country,
    count(distinct icao24) as aircraft,
    count(distinct icao24) filter (where not on_ground) as airborne_aircraft,
    count(*) as positions,
    round(avg(altitude_m) filter (where not on_ground)) as avg_altitude_m
from {{ ref('stg_aircraft_states') }}
group by all
