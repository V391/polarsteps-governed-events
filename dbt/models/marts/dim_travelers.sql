with signups as (
    select * from {{ ref('stg_user_signed_up') }}
),
installs as (
    select * from {{ ref('stg_installs') }}
    where user_key is not null
    qualify row_number() over (partition by user_key order by installed_at) = 1
),
first_trip as (
    select user_key, min(created_at) as first_trip_at from {{ ref('stg_trip_created') }} group by 1
),
first_step as (
    select user_key, min(stepped_at) as first_step_at from {{ ref('stg_step_added') }} group by 1
)
select
    s.user_key,
    s.signed_up_at,
    s.signup_method,
    s.platform,
    s.geo_country,
    s.marketing_consent,
    i.installed_at,
    coalesce(i.media_source, 'unknown') as reported_channel,
    i.campaign,
    i.att_status,
    t.first_trip_at,
    f.first_step_at,
    coalesce(t.first_trip_at <= s.signed_up_at + interval 7 day
             and f.first_step_at <= s.signed_up_at + interval 7 day, false) as activated_7d
from signups s
left join installs i using (user_key)
left join first_trip t using (user_key)
left join first_step f using (user_key)
