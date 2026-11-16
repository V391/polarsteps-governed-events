-- spend and SKAN postbacks on one grain: day x network x campaign x platform
with spend as (
    select cast(date as date) as day, network, campaign, sum(spend_eur) as spend_eur, 0 as skan_installs, 0 as skan_signups
    from {{ source('governed', 'ad_spend') }}
    group by all
),
skan as (
    select cast(postback_date as date) as day, network, campaign, 0 as spend_eur,
           sum(installs) as skan_installs,
           sum(case when conversion_value >= 1 then installs else 0 end) as skan_signups
    from {{ source('governed', 'skan_postbacks') }}
    group by all
)
select
    day, network as channel, campaign,
    case when contains(campaign, '_ios_') then 'ios' when contains(campaign, '_android_') then 'android' end as platform,
    sum(spend_eur) as spend_eur,
    sum(skan_installs) as skan_installs,
    sum(skan_signups) as skan_signups
from (select * from spend union all select * from skan)
group by all
