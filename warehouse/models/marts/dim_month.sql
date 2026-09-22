-- The month spine. Built from the data rather than a fixed range, so a month
-- with no activity at all still gets a row and a metric returns a zero instead
-- of a gap. A gap and a zero mean different things and a chart cannot tell
-- them apart.

with bounds as (
    select
        least(
            (select min(signup_month) from {{ ref('stg_accounts') }}),
            (select min(occurred_month) from {{ ref('stg_events') }})
        ) as first_month,
        greatest(
            (select max(signup_month) from {{ ref('stg_accounts') }}),
            (select max(occurred_month) from {{ ref('stg_events') }})
        ) as last_month
)

select
    cast(m as date)                as month,
    strftime(m, '%Y-%m')           as month_key,
    year(m)                        as year,
    month(m)                       as month_of_year,
    -- The last month of the window is almost always partial. Anything that
    -- compares months has to be able to exclude it; nothing downstream should
    -- have to rediscover which one it is.
    m = (select last_month from bounds) as is_partial
from bounds, unnest(generate_series(first_month, last_month, interval 1 month)) as t(m)
