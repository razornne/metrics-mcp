-- The only model that cleans anything, and it cleans exactly two things.
--
-- 1. Exact duplicates. A replicated event table gets them when a sync replays
--    a window, and they are invisible downstream: every count is simply a
--    little too high. `qualify row_number()` keeps the first of each identical
--    row. There is a test asserting event_id is unique afterwards, so if this
--    clause is ever dropped the build fails rather than the numbers drifting.
--
-- 2. Timestamps in the future, which come from a client clock that is wrong.
--    They are not a rounding problem — one event dated 2029 silently extends
--    every month spine built off max(occurred_at).
--
-- Neither is repaired further up. A metric should not have to know that its
-- source needs cleaning.

select
    event_id,
    user_id,
    account_id,
    event_name,
    occurred_at,
    cast(occurred_at as date)               as occurred_on,
    date_trunc('month', cast(occurred_at as date)) as occurred_month,
    event_name in (
        'idea_submitted',
        'insight_linked',
        'feature_prioritized',
        'roadmap_shared',
        'comment_added',
        'integration_connected',
        'report_exported'
    )                                        as is_terminal
from (
    select
        event_id,
        user_id,
        account_id,
        event_name,
        cast(occurred_at as timestamp) as occurred_at
    from {{ source('raw', 'events') }}
)
where occurred_at <= current_timestamp
qualify row_number() over (
    partition by event_id, user_id, account_id, event_name, occurred_at
    order by event_id
) = 1
