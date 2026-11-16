-- photo_uploaded, trip_shared, travel_book_ordered in one long table
select
    event_id,
    event_name,
    user_key,
    platform,
    event_ts,
    {{ prop('trip_id') }} as trip_id,
    {{ prop('photo_count', 'integer') }} as photo_count,
    {{ prop('channel') }} as share_channel,
    {{ prop('book_id') }} as book_id,
    {{ prop('price_eur', 'decimal(10,2)') }} as price_eur
from {{ ref('stg_events') }}
where event_name in ('photo_uploaded', 'trip_shared', 'travel_book_ordered')
