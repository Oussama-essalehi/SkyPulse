{% macro haversine_km(lat1, lon1, lat2, lon2) -%}
    (2 * 6371.0 * asin(least(1.0, sqrt(
        pow(sin(radians(({{ lat2 }}) - ({{ lat1 }})) / 2), 2)
        + cos(radians({{ lat1 }})) * cos(radians({{ lat2 }}))
        * pow(sin(radians(({{ lon2 }}) - ({{ lon1 }})) / 2), 2)
    ))))
{%- endmacro %}

{# Coarse geographic zones (bounding boxes) used for traffic aggregates. #}
{% macro geo_zone(lat, lon) -%}
    case
        when {{ lat }} between 35 and 72 and {{ lon }} between -25 and 45 then 'Europe'
        when {{ lat }} between 12 and 72 and {{ lon }} between -170 and -50 then 'North America'
        when {{ lat }} between -10 and 12 and {{ lon }} between -120 and -60 then 'Central America & Caribbean'
        when {{ lat }} between -60 and 12 and {{ lon }} between -90 and -30 then 'South America'
        when {{ lat }} between 12 and 42 and {{ lon }} between 25 and 63 then 'Middle East'
        when {{ lat }} between -40 and 37 and {{ lon }} between -20 and 55 then 'Africa'
        when {{ lat }} between -12 and 55 and {{ lon }} between 60 and 150 then 'Asia'
        when {{ lat }} between -50 and -10 and {{ lon }} between 110 and 180 then 'Oceania'
        when {{ lat }} > 60 then 'Arctic'
        else 'Oceans & other'
    end
{%- endmacro %}

{# Initial great-circle bearing from point 1 to point 2, degrees clockwise from north. #}
{% macro bearing_deg(lat1, lon1, lat2, lon2) -%}
    ((degrees(atan2(
        sin(radians(({{ lon2 }}) - ({{ lon1 }}))) * cos(radians({{ lat2 }})),
        cos(radians({{ lat1 }})) * sin(radians({{ lat2 }}))
        - sin(radians({{ lat1 }})) * cos(radians({{ lat2 }})) * cos(radians(({{ lon2 }}) - ({{ lon1 }})))
    )) + 360) % 360)
{%- endmacro %}
