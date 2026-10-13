-- Usual route of each callsign: most frequent arrival per (callsign, departure).
-- Used to flag diversions and to guess the destination of live aircraft.
with counts as (
    select callsign, departure_airport, arrival_airport, count(*) as flights,
           max(arrival_time) as last_seen
    from {{ ref('fct_flights') }}
    where is_complete and callsign is not null
    group by all
),

ranked as (
    select
        *,
        sum(flights) over (partition by callsign, departure_airport) as route_flights,
        row_number() over (
            partition by callsign, departure_airport order by flights desc, last_seen desc
        ) as rank
    from counts
)

select
    callsign,
    departure_airport,
    arrival_airport as usual_arrival_airport,
    flights as usual_arrival_flights,
    route_flights,
    round(flights / route_flights, 3) as usual_arrival_share,
    last_seen
from ranked
where rank = 1
