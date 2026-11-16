select
    event_id,
    user_key,
    platform,
    app_version,
    event_ts as stepped_at,
    {{ prop('trip_id') }} as trip_id,
    {{ prop('step_id') }} as step_id,
    {{ prop('lat', 'double') }} as lat_1km,
    {{ prop('lon', 'double') }} as lon_1km,
    {{ prop('location_suppressed', 'boolean') }} as location_suppressed,
    {{ prop('country_code') }} as country_code,
    {{ prop('has_photo', 'boolean') }} as has_photo,
    {{ prop('step_text') }} as step_text
from {{ ref('stg_events') }}
where event_name = 'step_added'
