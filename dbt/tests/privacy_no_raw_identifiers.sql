-- Fails if a raw app user id, device id, IP or email column shows up anywhere in the governed events.
select event_id
from {{ ref('stg_events') }}
where user_key like 'u\_%' escape '\'
   or device_key like 'd-%'
   or json_extract_string(properties, '$.email') is not null
   or json_extract_string(properties, '$.ip') is not null
