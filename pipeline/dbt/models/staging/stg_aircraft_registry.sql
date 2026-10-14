-- OpenSky aircraft database: one row per transponder address (icao24).
with src as (
    {% if reference_has_file('aircraft_database.csv') %}
    select *
    from read_csv('{{ reference_path("aircraft_database.csv") }}', header = true, all_varchar = true)
    {% else %}
    select
        null::varchar as icao24, null::varchar as registration, null::varchar as manufacturericao,
        null::varchar as manufacturername, null::varchar as model, null::varchar as typecode,
        null::varchar as icaoaircrafttype, null::varchar as operator,
        null::varchar as operatoricao, null::varchar as owner, null::varchar as built
    where false
    {% endif %}
)

select
    lower(trim(icao24)) as icao24,
    nullif(trim(registration), '') as registration,
    upper(nullif(trim(typecode), '')) as typecode,
    coalesce(nullif(trim(manufacturername), ''), nullif(trim(manufacturericao), '')) as manufacturer,
    nullif(trim(model), '') as model,
    nullif(trim(icaoaircrafttype), '') as icao_aircraft_class,
    nullif(trim(operator), '') as operator,
    upper(nullif(trim(operatoricao), '')) as operator_icao,
    nullif(trim(owner), '') as owner,
    try_cast(left(built, 4) as integer) as built_year
from src
where regexp_matches(lower(trim(icao24)), '^[0-9a-f]{6}$')
qualify row_number() over (
    partition by lower(trim(icao24)) order by (typecode is null or typecode = ''), registration
) = 1
