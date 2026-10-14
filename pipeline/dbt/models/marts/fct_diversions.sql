-- Diversions: the reconstructed arrival differs from the usual destination of the same
-- callsign from the same departure airport (>= 3 previous flights, >= 60 % share).
select
    f.flight_id,
    f.icao24,
    f.callsign,
    f.departure_airport,
    f.arrival_airport,
    f.arrival_airport_name,
    r.usual_arrival_airport,
    r.usual_arrival_flights,
    r.usual_arrival_share,
    f.departure_time,
    f.arrival_time,
    a.latitude as arrival_latitude,
    a.longitude as arrival_longitude
from {{ ref('fct_flights') }} as f
inner join {{ ref('fct_callsign_routes') }} as r
    on r.callsign = f.callsign and r.departure_airport = f.departure_airport
left join (
    select airport_icao, any_value(latitude) as latitude, any_value(longitude) as longitude
    from {{ ref('stg_airports') }}
    group by airport_icao
) as a on a.airport_icao = f.arrival_airport
where f.is_complete
  and f.arrival_airport <> r.usual_arrival_airport
  and r.usual_arrival_flights >= 3
  and r.usual_arrival_share >= 0.6
