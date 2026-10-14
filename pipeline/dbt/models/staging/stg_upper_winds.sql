-- 250 hPa winds per grid point and hour; the latest fetch wins (forecast -> analysis).
with raw as (
    {% if lake_has_files('upper_winds') %}
    select * from {{ source('lake', 'upper_winds') }}
    {% else %}
    select
        null::smallint as grid_lat, null::smallint as grid_lon, null::timestamp as valid_time,
        null::float as wind_speed_ms, null::float as wind_direction_deg,
        null::double as fetched_at
    where false
    {% endif %}
)

select
    cast(grid_lat as smallint) as grid_lat,
    cast(grid_lon as smallint) as grid_lon,
    -- `valid_time`, not `hour`: the lake partition key is already called hour.
    cast(valid_time as timestamp) as hour,
    wind_speed_ms,
    wind_direction_deg
from raw
where wind_speed_ms is not null
qualify row_number() over (
    partition by grid_lat, grid_lon, valid_time order by fetched_at desc
) = 1
