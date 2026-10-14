-- Every aircraft of the OpenSky database with its ICAO type details (~500k rows).
-- Read by the API for point lookups (aircraft panel) and by the assistant.
select
    r.icao24,
    r.registration,
    r.typecode,
    coalesce(r.manufacturer, t.type_manufacturer) as manufacturer,
    coalesce(r.model, t.type_name) as model,
    t.type_name,
    t.wake_category,
    t.engine_type,
    t.engine_count,
    coalesce(r.icao_aircraft_class, t.icao_aircraft_class) as icao_aircraft_class,
    r.operator,
    r.operator_icao,
    r.owner,
    r.built_year
from {{ ref('stg_aircraft_registry') }} as r
left join {{ ref('stg_aircraft_types') }} as t using (typecode)
