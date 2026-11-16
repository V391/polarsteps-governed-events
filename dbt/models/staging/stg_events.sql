select
    event_id,
    event_name,
    user_key,
    device_key,
    platform,
    app_version,
    cast(event_ts as timestamp) as event_ts,
    cast(received_ts as timestamp) as received_ts,
    geo_country,
    properties
from {{ source('governed', 'events') }}
