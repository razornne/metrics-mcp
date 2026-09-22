-- Account × month. One row per account per month it existed, active or not.
--
-- Every ratio this warehouse exposes is a pair of columns on this table, not a
-- rate stored per row. `activation_rate` is sum(activated) / sum(eligible),
-- never avg(rate). The difference is not cosmetic: averaging per-account rates
-- gives an account with one user the same weight as one with forty, and the
-- number quietly stops being the thing its name claims. The registry can only
-- express ratios as numerator and denominator for this reason — see
-- metrics/_catalog.yml.

with spine as (
    select
        a.account_id,
        a.plan,
        a.country,
        a.signup_month,
        m.month,
        m.is_partial
    from {{ ref('stg_accounts') }} a
    cross join {{ ref('dim_month') }} m
    where m.month >= a.signup_month
),

activity as (
    select
        account_id,
        occurred_month                                     as month,
        count(*)                                           as n_events,
        count(*) filter (where is_terminal)                as n_terminal_events,
        count(distinct user_id)                            as n_active_users,
        count(distinct user_id) filter (where is_terminal) as n_contributing_users,
        max(event_name = 'feature_prioritized')            as used_prioritization,
        max(event_name = 'integration_connected')          as used_integration
    from {{ ref('stg_events') }}
    group by 1, 2
),

-- Activation is measured on the account's first seven days, and it counts a
-- terminal action only: opening a screen is not evidence that anyone got
-- value. The window is anchored to the account's own signup, so an account
-- that signs up on the 28th is not judged on three days.
first_terminal as (
    select
        e.account_id,
        min(e.occurred_at) as first_terminal_at
    from {{ ref('stg_events') }} e
    where e.is_terminal
    group by 1
),

activation as (
    select
        a.account_id,
        a.signup_month,
        ft.first_terminal_at is not null
            and ft.first_terminal_at <= a.signed_up_at + interval 7 day as activated_7d
    from {{ ref('stg_accounts') }} a
    left join first_terminal ft using (account_id)
),

subs as (
    select account_id, month, mrr from {{ ref('stg_subscriptions') }}
)

select
    s.account_id,
    s.month,
    s.plan,
    s.country,

    coalesce(act.n_events, 0)             as n_events,
    coalesce(act.n_terminal_events, 0)    as n_terminal_events,
    coalesce(act.n_active_users, 0)       as n_active_users,
    coalesce(act.n_contributing_users, 0) as n_contributing_users,

    coalesce(act.n_events, 0) > 0                                as is_active,
    coalesce(act.used_prioritization, false)                     as used_prioritization,
    coalesce(act.used_integration, false)                        as used_integration,

    s.month = s.signup_month                                     as is_new,
    -- Eligible for the activation question only in the signup month; asking it
    -- of month nine would answer a different question with the same name.
    s.month = s.signup_month                                     as is_activation_eligible,
    s.month = s.signup_month and coalesce(a.activated_7d, false) as is_activated_7d,

    coalesce(sub.mrr, 0)                                         as mrr,
    coalesce(sub.mrr, 0) > 0                                     as is_paying,

    s.is_partial
from spine s
left join activity act on act.account_id = s.account_id and act.month = s.month
left join activation a on a.account_id = s.account_id
left join subs sub on sub.account_id = s.account_id and sub.month = s.month
