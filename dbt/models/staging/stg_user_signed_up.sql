select
    event_id,
    user_key,
    platform,
    geo_country,
    event_ts as signed_up_at,
    {{ prop('signup_method') }} as signup_method,
    {{ prop('marketing_consent', 'boolean') }} as marketing_consent
from {{ ref('stg_events') }}
where event_name = 'user_signed_up'
qualify row_number() over (partition by user_key order by event_ts) = 1
