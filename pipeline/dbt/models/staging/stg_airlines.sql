-- OpenFlights airlines: ICAO designator -> airline (active airlines first).
with src as (
    {% if reference_has_file('airlines.dat') %}
    select *
    from read_csv(
        '{{ reference_path("airlines.dat") }}',
        header = false,
        nullstr = '\N',
        columns = {
            'airline_id': 'INTEGER', 'name': 'VARCHAR', 'alias': 'VARCHAR', 'iata': 'VARCHAR',
            'icao': 'VARCHAR', 'callsign': 'VARCHAR', 'country': 'VARCHAR', 'active': 'VARCHAR'
        }
    )
    {% else %}
    select
        null::integer as airline_id, null::varchar as name, null::varchar as alias,
        null::varchar as iata, null::varchar as icao, null::varchar as callsign,
        null::varchar as country, null::varchar as active
    where false
    {% endif %}
)

select
    icao as airline_icao,
    nullif(trim(iata), '') as airline_iata,
    trim(name) as airline_name,
    nullif(trim(callsign), '') as radio_callsign,
    nullif(trim(country), '') as airline_country,
    active = 'Y' as is_active
from src
where regexp_matches(icao, '^[A-Z]{3}$')
-- A defunct airline may share its code with an active one: prefer the active one.
qualify row_number() over (partition by icao order by active = 'Y' desc, airline_id) = 1
