select
    install_key,
    nullif(user_key, '') as user_key,
    platform,
    cast(install_ts as timestamp) as installed_at,
    media_source,
    nullif(campaign, '') as campaign,
    att_status,
    country_code
from {{ source('governed', 'installs') }}
