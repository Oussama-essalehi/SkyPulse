-- Airlines by ICAO designator (OpenFlights), e.g. AFR -> Air France.
select * from {{ ref('stg_airlines') }}
