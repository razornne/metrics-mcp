select
    user_id,
    account_id,
    cast(joined_at as timestamp)              as joined_at,
    date_trunc('month', cast(joined_at as date)) as join_month,
    role
from {{ source('raw', 'users') }}
