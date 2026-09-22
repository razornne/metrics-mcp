"""Generate the synthetic product-usage dataset this repo runs on.

Why synthetic. The subject of this repo is the contract between a metric
registry, a warehouse and an agent — not the dataset. CI has to build the
warehouse and run the evals on every push, so the data has to appear without a
download, a licence or a credential. It is generated from a fixed seed, so the
same numbers come out on my machine and in CI, and a test can assert on them.

What it is not. It is not a real product's traffic, and no conclusion about
real user behaviour should be drawn from it. The shapes below (weekday
seasonality, plan-dependent intensity, a churn hazard that rises when usage
falls) are there so the metrics have something to measure, not because they are
a model of anything.

    python data/generate.py            # writes data/raw/*.parquet

One deliberate wrinkle: about 0.4% of the event rows are exact duplicates, and
a handful carry a timestamp in the future. Both are things that happen in real
replicated event tables, and staging is expected to remove them — there is a
test that fails if it stops doing so.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

SEED = 20260922
START = datetime(2024, 10, 1)
MONTHS = 24
N_ACCOUNTS = 600

OUT = Path(__file__).parent / "raw"

PLANS = [("free", 0.55, 0), ("pro", 0.35, 49), ("enterprise", 0.10, 399)]
COUNTRIES = ["CZ", "DE", "US", "GB", "PL", "NL", "ES", "UA"]

# The event vocabulary. `terminal` marks an action that completes something,
# as opposed to looking at a screen. Activation and adoption are defined on
# terminal events only; see metrics/_catalog.yml for why.
EVENTS = [
    # name,                  terminal, weight
    ("workspace_viewed", False, 30.0),
    ("insight_viewed", False, 14.0),
    ("roadmap_viewed", False, 12.0),
    ("idea_submitted", True, 6.0),
    ("insight_linked", True, 4.0),
    ("feature_prioritized", True, 3.0),
    ("roadmap_shared", True, 1.2),
    ("comment_added", True, 5.0),
    ("integration_connected", True, 0.5),
    ("report_exported", True, 0.8),
]


def _month_add(d: datetime, n: int) -> datetime:
    y, m = divmod((d.year * 12 + d.month - 1) + n, 12)
    return d.replace(year=y, month=m + 1, day=1)


def generate() -> None:
    rng = random.Random(SEED)
    end = _month_add(START, MONTHS)

    accounts, users, events, subs = [], [], [], []

    for a in range(1, N_ACCOUNTS + 1):
        account_id = f"acc_{a:04d}"
        # Signups spread across the window, denser later — a growing product.
        offset_days = int((rng.random() ** 0.7) * (MONTHS * 30))
        signed_up = START + timedelta(days=offset_days, hours=rng.randint(8, 19))
        if signed_up >= end:
            continue

        r, plan, mrr_unit = rng.random(), None, 0
        acc = 0.0
        for name, share, price in PLANS:
            acc += share
            if r <= acc:
                plan, mrr_unit = name, price
                break

        seats = {"free": (1, 3), "pro": (2, 12), "enterprise": (8, 40)}[plan]
        n_users = rng.randint(*seats)
        country = rng.choice(COUNTRIES)

        accounts.append((account_id, plan, country, signed_up, n_users))

        # How engaged this account is, and how long it lives.
        intensity = {"free": 0.35, "pro": 1.0, "enterprise": 1.8}[plan] * rng.uniform(0.3, 1.9)
        # Lifetime in days; free accounts churn much faster.
        base_life = {"free": 90, "pro": 400, "enterprise": 900}[plan]
        life_days = max(7, int(rng.expovariate(1 / base_life)))
        churn_at = signed_up + timedelta(days=life_days)

        account_user_ids = []
        for u in range(n_users):
            user_id = f"usr_{a:04d}_{u:02d}"
            # The first user is the signer-up; the rest trickle in.
            joined = signed_up + timedelta(days=0 if u == 0 else rng.randint(0, 60))
            if joined >= end:
                continue
            account_user_ids.append((user_id, joined))
            users.append((user_id, account_id, joined, "admin" if u == 0 else "member"))

        # Subscription months, one row per account-month while alive.
        m = datetime(signed_up.year, signed_up.month, 1)
        while m < min(end, _month_add(churn_at, 1)):
            subs.append((account_id, m.date(), plan, mrr_unit * len(account_user_ids)))
            m = _month_add(m, 1)

        # Events.
        for user_id, joined in account_user_ids:
            # Some users never do anything at all — a real and important case.
            if rng.random() < 0.18:
                continue
            day = joined
            while day < min(end, churn_at):
                # Weekday seasonality, and decay as the account ages.
                dow = day.weekday()
                weekday_factor = 0.25 if dow >= 5 else 1.0
                age_days = (day - joined).days
                decay = 0.35 + 0.65 * (0.997**age_days)
                lam = intensity * weekday_factor * decay * rng.uniform(0.5, 1.5)
                for _ in range(int(rng.expovariate(1 / max(lam, 0.05)))):
                    r2, pick = rng.random() * sum(w for _, _, w in EVENTS), None
                    acc2 = 0.0
                    for name, _term, w in EVENTS:
                        acc2 += w
                        if r2 <= acc2:
                            pick = name
                            break
                    ts = day + timedelta(
                        hours=rng.randint(7, 21),
                        minutes=rng.randint(0, 59),
                        seconds=rng.randint(0, 59),
                    )
                    if ts >= end:
                        continue
                    events.append((f"evt_{len(events):08d}", user_id, account_id, pick, ts))
                day += timedelta(days=1)

    # Two defects staging is expected to clean, on purpose. See the docstring.
    n_dupes = int(len(events) * 0.004)
    for _ in range(n_dupes):
        events.append(events[rng.randrange(len(events))])
    for _ in range(12):
        e = list(events[rng.randrange(len(events))])
        e[0] = f"evt_future_{rng.randrange(10**6):06d}"
        e[4] = end + timedelta(days=rng.randint(30, 900))
        events.append(tuple(e))

    # Written as CSV and handed to DuckDB to convert. Inserting a million rows
    # through executemany takes minutes; COPY from a CSV takes under a second,
    # and CI runs this on every push.
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    tmp = OUT / "_tmp.csv"

    tables = [
        (
            "accounts",
            accounts,
            ["account_id", "plan", "country", "signed_up_at", "seats_purchased"],
        ),
        ("users", users, ["user_id", "account_id", "joined_at", "role"]),
        ("events", events, ["event_id", "user_id", "account_id", "event_name", "occurred_at"]),
        ("subscriptions", subs, ["account_id", "month", "plan", "mrr"]),
    ]
    for name, rows, cols in tables:
        with tmp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            w.writerows(rows)
        con.execute(
            f"COPY (SELECT * FROM read_csv('{tmp.as_posix()}', header=true)) "  # noqa: S608
            f"TO '{(OUT / f'{name}.parquet').as_posix()}' (FORMAT PARQUET)"
        )
        n = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{(OUT / f'{name}.parquet').as_posix()}')"  # noqa: S608
        ).fetchone()[0]
        print(f"{name:14s} {n:>9,} rows")
    tmp.unlink(missing_ok=True)
    con.close()


if __name__ == "__main__":
    generate()
