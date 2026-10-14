-- Aircraft actually observed by SkyRadar, enriched with the OpenSky aircraft
-- database (type, operator) and the airline of their latest callsign.
with seen as (
    select
        icao24,
        arg_max(callsign, position_ts) filter (where callsign is not null) as last_callsign,
        arg_max(origin_country, position_ts) as origin_country,
        min(position_ts) as first_seen_ts,
        max(position_ts) as last_seen_ts,
        count(*) as n_positions
    from {{ ref('stg_aircraft_states') }}
    group by icao24
)

select
    s.icao24,
    s.last_callsign,
    s.origin_country,
    r.registration,
    r.typecode,
    r.manufacturer,
    r.model,
    r.type_name,
    r.wake_category,
    r.engine_type,
    r.engine_count,
    r.operator,
    coalesce({{ airline_icao('s.last_callsign') }}, r.operator_icao) as airline_icao,
    a.airline_name,
    r.built_year,
    r.icao24 is not null as in_registry,
    s.first_seen_ts,
    s.last_seen_ts,
    s.n_positions
from seen as s
left join {{ ref('dim_aircraft_registry') }} as r using (icao24)
left join {{ ref('stg_airlines') }} as a
    on a.airline_icao = coalesce({{ airline_icao('s.last_callsign') }}, r.operator_icao)
