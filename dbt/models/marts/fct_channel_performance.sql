-- Month x platform x channel. iOS installs without ATT consent arrive at user level as "organic";
-- their paid source is only visible in aggregated SKAN postbacks. This model moves them back:
-- paid channels gain the SKAN installs, iOS organic loses the same number.
with user_level as (
    select
        date_trunc('month', i.installed_at) as month,
        i.platform,
        i.media_source as channel,
        count(*) as installs_user_level,
        count(i.user_key) as signups_user_level,
        count(*) filter (where t.activated_7d) as activated_7d_user_level
    from {{ ref('stg_installs') }} i
    left join {{ ref('dim_travelers') }} t using (user_key)
    group by all
),
marketing as (
    select date_trunc('month', day) as month, platform, channel,
           sum(spend_eur) as spend_eur, sum(skan_installs) as skan_installs, sum(skan_signups) as skan_signups
    from {{ ref('stg_marketing') }}
    group by all
),
joined as (
    select
        coalesce(u.month, m.month) as month,
        coalesce(u.platform, m.platform) as platform,
        coalesce(u.channel, m.channel) as channel,
        coalesce(m.spend_eur, 0) as spend_eur,
        coalesce(u.installs_user_level, 0) as installs_user_level,
        coalesce(u.signups_user_level, 0) as signups_user_level,
        coalesce(u.activated_7d_user_level, 0) as activated_7d_user_level,
        coalesce(m.skan_installs, 0) as skan_installs,
        coalesce(m.skan_signups, 0) as skan_signups
    from user_level u
    full outer join marketing m using (month, platform, channel)
),
adjusted as (
    select
        *,
        case when channel = 'organic'
             then installs_user_level - sum(skan_installs) over (partition by month, platform)
             else installs_user_level + skan_installs end as installs_total,
        case when channel = 'organic'
             then signups_user_level - sum(skan_signups) over (partition by month, platform)
             else signups_user_level + skan_signups end as signups_total
    from joined
)
select
    cast(month as date) as month,
    platform,
    channel,
    round(spend_eur, 2) as spend_eur,
    installs_user_level,
    skan_installs,
    installs_total,
    signups_user_level,
    signups_total,
    activated_7d_user_level,
    round(spend_eur / nullif(installs_user_level, 0), 2) as cpi_naive_eur,
    round(spend_eur / nullif(installs_total, 0), 2) as cpi_eur,
    round(spend_eur / nullif(signups_total, 0), 2) as cost_per_signup_eur
from adjusted
