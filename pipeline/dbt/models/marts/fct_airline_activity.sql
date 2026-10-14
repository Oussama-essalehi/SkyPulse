-- Hourly activity per airline, from the callsign prefix (AFR1234 -> AFR).
with positions as (
    select
        date_trunc('hour', position_ts) as hour,
        {{ airline_icao('callsign') }} as airline_icao,
        icao24,
        on_ground
    from {{ ref('stg_aircraft_states') }}
)

select
    p.hour,
    p.airline_icao,
    coalesce(a.airline_name, p.airline_icao) as airline_name,
    a.airline_country,
    count(distinct p.icao24) as aircraft,
    count(distinct p.icao24) filter (where not p.on_ground) as airborne_aircraft,
    count(*) as positions
from positions as p
left join {{ ref('stg_airlines') }} as a using (airline_icao)
where p.airline_icao is not null
group by all
