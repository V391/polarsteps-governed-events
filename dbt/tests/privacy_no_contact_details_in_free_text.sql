-- Fails if any email address or phone number survived the gate into free text fields.
select 'step_text' as field, event_id, step_text as value
from {{ ref('stg_step_added') }}
where regexp_matches(step_text, '[\w.+-]+@[\w-]+\.[\w.-]+') or regexp_matches(step_text, '\+?\d[\d\s().-]{7,}\d')
union all
select 'trip_name', event_id, trip_name
from {{ ref('stg_trip_created') }}
where regexp_matches(trip_name, '[\w.+-]+@[\w-]+\.[\w.-]+') or regexp_matches(trip_name, '\+?\d[\d\s().-]{7,}\d')
