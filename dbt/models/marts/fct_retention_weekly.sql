with cohorts as (
    select user_key, date_trunc('week', signed_up_at) as cohort_week from {{ ref('dim_travelers') }}
),
activity as (
    select distinct e.user_key, date_diff('week', c.cohort_week, date_trunc('week', e.event_ts)) as week_number
    from {{ ref('stg_events') }} e
    join cohorts c using (user_key)
),
sizes as (
    select cohort_week, count(*) as cohort_size from cohorts group by 1
)
select
    c.cohort_week,
    a.week_number,
    s.cohort_size,
    count(distinct a.user_key) as active_travelers,
    round(count(distinct a.user_key) / s.cohort_size, 4) as retention_rate
from activity a
join cohorts c using (user_key)
join sizes s using (cohort_week)
where a.week_number between 0 and 12
group by 1, 2, 3
