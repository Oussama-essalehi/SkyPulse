-- ICAO Doc 8643 aircraft type designators (one row per designator).
with src as (
    {% if reference_has_file('aircraft_types.csv') %}
    select *
    from read_csv('{{ reference_path("aircraft_types.csv") }}', header = true, all_varchar = true)
    {% else %}
    select
        null::varchar as AircraftDescription, null::varchar as Description,
        null::varchar as Designator, null::varchar as EngineCount, null::varchar as EngineType,
        null::varchar as ManufacturerCode, null::varchar as ModelFullName, null::varchar as WTC
    where false
    {% endif %}
)

select
    upper(trim(Designator)) as typecode,
    trim(ModelFullName) as type_name,
    trim(ManufacturerCode) as type_manufacturer,
    AircraftDescription as aircraft_description,
    Description as icao_aircraft_class,
    try_cast(EngineCount as integer) as engine_count,
    EngineType as engine_type,
    -- Wake turbulence category: L(ight), M(edium), H(eavy), J (super, A380).
    nullif(trim(WTC), '') as wake_category
from src
where nullif(trim(Designator), '') is not null
-- Several model names share a designator: keep the shortest one (deterministic).
qualify row_number() over (
    partition by upper(trim(Designator)) order by length(ModelFullName), ModelFullName
) = 1
