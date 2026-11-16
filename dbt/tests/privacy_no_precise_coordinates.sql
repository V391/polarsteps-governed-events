-- Fails if a coordinate finer than the ~1 km grid, or a coordinate inside a suppressed home zone, reaches the warehouse.
select event_id, lat_1km, lon_1km, location_suppressed
from {{ ref('stg_step_added') }}
where (location_suppressed and (lat_1km is not null or lon_1km is not null))
   or round(lat_1km, 2) <> lat_1km
   or round(lon_1km, 2) <> lon_1km
