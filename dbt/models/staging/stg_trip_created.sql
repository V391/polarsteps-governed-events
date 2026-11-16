select
    event_id,
    user_key,
    platform,
    event_ts as created_at,
    {{ prop('trip_id') }} as trip_id,
    {{ prop('planned_duration_days', 'integer') }} as planned_duration_days,
    {{ prop('is_public', 'boolean') }} as is_public,
    {{ prop('trip_name') }} as trip_name
from {{ ref('stg_events') }}
where event_name = 'trip_created'
