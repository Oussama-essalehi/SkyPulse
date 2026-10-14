-- Recent 250 hPa wind grid (last 48 h), read by the API to compute the ETA model's
-- tail-wind feature for live aircraft.
select *
from {{ ref('stg_upper_winds') }}
where hour >= (select max(hour) from {{ ref('stg_upper_winds') }}) - interval 48 hour
