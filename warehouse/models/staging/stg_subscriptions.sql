select
    account_id,
    cast(month as date) as month,
    plan,
    cast(mrr as integer) as mrr
from {{ source('raw', 'subscriptions') }}
