-- Warn level health check: any app version where more than 5% of events land in quarantine.
{{ config(severity='warn') }}
with q as (select app_version, platform, count(*) as quarantined from {{ source('governed', 'quarantine') }} group by all),
a as (select app_version, platform, count(*) as accepted from {{ ref('stg_events') }} group by all)
select platform, app_version, quarantined, accepted, round(quarantined / (quarantined + coalesce(accepted, 0)), 3) as rate
from q left join a using (app_version, platform)
where quarantined / (quarantined + coalesce(accepted, 0)) > 0.05
