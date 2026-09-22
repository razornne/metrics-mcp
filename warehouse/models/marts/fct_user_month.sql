-- User × month, for the metrics whose unit is a person rather than an account.
--
-- Kept separate from fct_account_month rather than derived from it, because a
-- distinct count of users does not survive aggregation: summing per-account
-- active-user counts double-counts nobody here (a user belongs to one account)
-- but would the moment that stopped being true, and the model should not
-- depend on a property of the data that nothing enforces.

with spine as (
    select
        u.user_id,
        u.account_id,
        a.plan,
        a.country,
        u.join_month,
        m.month,
        m.is_partial
    from {{ ref('stg_users') }} u
    join {{ ref('stg_accounts') }} a using (account_id)
    cross join {{ ref('dim_month') }} m
    where m.month >= u.join_month
),

activity as (
    select
        user_id,
        occurred_month                      as month,
        count(*)                            as n_events,
        count(*) filter (where is_terminal) as n_terminal_events
    from {{ ref('stg_events') }}
    group by 1, 2
),

prev as (
    select user_id, month, n_events from activity
)

select
    s.user_id,
    s.account_id,
    s.month,
    s.plan,
    s.country,

    coalesce(act.n_events, 0)          as n_events,
    coalesce(act.n_terminal_events, 0) as n_terminal_events,
    coalesce(act.n_events, 0) > 0      as is_active,
    coalesce(act.n_terminal_events, 0) > 0 as is_contributing,
    s.month = s.join_month             as is_new,

    -- Retention pair: eligible means the user was active last month, retained
    -- means they were active this month too. Summing the two columns and
    -- dividing gives month-over-month retention at any cut; storing a rate per
    -- user could not be aggregated at all.
    coalesce(p.n_events, 0) > 0                                     as was_active_prev_month,
    coalesce(p.n_events, 0) > 0 and coalesce(act.n_events, 0) > 0   as is_retained,

    s.is_partial
from spine s
left join activity act on act.user_id = s.user_id and act.month = s.month
left join prev p on p.user_id = s.user_id and p.month = s.month - interval 1 month
