select
    account_id,
    plan,
    country,
    cast(signed_up_at as timestamp)              as signed_up_at,
    date_trunc('month', cast(signed_up_at as date)) as signup_month,
    cast(seats_purchased as integer)             as seats_purchased
from {{ source('raw', 'accounts') }}
