with steps as (
    select
        *,
        lag(lat_1km) over w as prev_lat,
        lag(lon_1km) over w as prev_lon
    from {{ ref('stg_step_added') }}
    where not location_suppressed
    window w as (partition by trip_id order by stepped_at)
),
step_stats as (
    select
        s.trip_id,
        count(*) as n_steps,
        count(distinct s.country_code) as n_countries,
        count(*) filter (where s.has_photo) as n_steps_with_photo,
        min(s.stepped_at) as first_step_at,
        max(s.stepped_at) as last_step_at
    from {{ ref('stg_step_added') }} s
    group by 1
),
distance as (
    select trip_id, sum({{ haversine_km('prev_lat', 'prev_lon', 'lat_1km', 'lon_1km') }}) as in_destination_km
    from steps
    where prev_lat is not null
    group by 1
),
other as (
    select
        trip_id,
        sum(photo_count) filter (where event_name = 'photo_uploaded') as n_photos,
        count(*) filter (where event_name = 'trip_shared') as n_shares,
        count(*) filter (where event_name = 'travel_book_ordered') as n_books,
        sum(price_eur) filter (where event_name = 'travel_book_ordered') as book_revenue_eur
    from {{ ref('stg_other_events') }}
    group by 1
)
select
    t.trip_id,
    t.user_key,
    t.platform,
    t.created_at,
    t.planned_duration_days,
    t.is_public,
    coalesce(s.n_steps, 0) as n_steps,
    coalesce(s.n_countries, 0) as n_countries,
    coalesce(s.n_steps_with_photo, 0) as n_steps_with_photo,
    s.first_step_at,
    s.last_step_at,
    round(coalesce(d.in_destination_km, 0), 1) as in_destination_km,
    cast(coalesce(o.n_photos, 0) as bigint) as n_photos,
    coalesce(o.n_shares, 0) as n_shares,
    coalesce(o.n_books, 0) > 0 as has_travel_book,
    cast(coalesce(o.book_revenue_eur, 0) as decimal(12, 2)) as book_revenue_eur
from {{ ref('stg_trip_created') }} t
left join step_stats s using (trip_id)
left join distance d using (trip_id)
left join other o using (trip_id)
