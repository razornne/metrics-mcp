"""Against the built warehouse. These run in CI, which builds it first."""

from __future__ import annotations

from datetime import date

import pytest

from metrics_mcp import warehouse
from metrics_mcp.registry import load, validate_against_warehouse

pytestmark = pytest.mark.skipif(
    not warehouse.DB_PATH.exists(), reason="warehouse not built; run `mx build`"
)


@pytest.fixture(scope="module")
def con():
    c = warehouse.connect()
    yield c
    c.close()


@pytest.fixture(scope="module")
def registry():
    return load()


def test_every_metric_resolves(registry, con):
    assert validate_against_warehouse(registry, con) == []


def test_a_dimension_outside_the_allow_list_is_refused(registry):
    m = registry.get("MX-001")
    with pytest.raises(warehouse.QueryError) as exc:
        warehouse.build_sql(m, "account_id")
    assert "cannot be cut by" in str(exc.value)


def test_every_declared_dimension_actually_works(registry, con):
    # A dimension listed in the registry that does not group is worse than one
    # that is missing: the agent is told it may ask, and the ask fails.
    lo, hi = warehouse.month_bounds(con)
    for m in registry.active:
        for dim in m.dimensions:
            result = warehouse.query(m, lo, hi, dim, con=con)
            assert result.rows, f"{m.id} by {dim} returned nothing"


def test_duplicates_are_gone_from_staging(con):
    # generate.py injects exact duplicates on purpose. If the dedupe in
    # stg_events is ever removed, this fails rather than the counts drifting.
    dupes = con.execute(
        "select count(*) from (select event_id from stg_events group by 1 having count(*) > 1)"
    ).fetchone()[0]
    assert dupes == 0
    assert con.execute("select count(*) from stg_events").fetchone()[0] > 100_000


def test_future_timestamps_are_gone(con):
    ahead = con.execute(
        "select count(*) from stg_events where occurred_at > current_timestamp"
    ).fetchone()[0]
    assert ahead == 0


def test_raw_really_did_contain_the_defects():
    # Guards the guard: if the generator stopped injecting them, the two tests
    # above would pass while proving nothing.
    import duckdb

    from metrics_mcp.cli import ROOT

    raw = (ROOT / "data" / "raw" / "events.parquet").as_posix()
    con = duckdb.connect()
    try:
        dupes = con.execute(
            f"select count(*) from (select event_id from read_parquet('{raw}') "  # noqa: S608
            "group by 1 having count(*) > 1)"
        ).fetchone()[0]
    finally:
        con.close()
    assert dupes > 0


def test_a_ratio_returns_its_components(registry, con):
    m = registry.get("MX-007")
    result = warehouse.query(m, date(2026, 1, 1), date(2026, 3, 1), con=con)
    for row in result.rows:
        assert {"numerator", "denominator"} <= row.keys()
        if row["value"] is not None:
            assert row["value"] == pytest.approx(row["numerator"] / row["denominator"], abs=1e-4)


def test_rolling_a_ratio_up_by_averaging_disagrees_with_the_truth(registry, con):
    """The reason the registry will not store a rate.

    Summing the components and dividing is the ratio over the period. Averaging
    the monthly rates is a different number, and it is the one people reach for.
    """
    m = registry.get("MX-007")
    result = warehouse.query(m, date(2026, 1, 1), date(2026, 6, 1), con=con)
    rows = [r for r in result.rows if r["value"] is not None]

    correct = sum(r["numerator"] for r in rows) / sum(r["denominator"] for r in rows)
    naive = sum(r["value"] for r in rows) / len(rows)

    assert correct != pytest.approx(naive, abs=1e-6)
    assert 0 < correct < 1


def test_the_partial_month_is_flagged(registry, con):
    lo, hi = warehouse.month_bounds(con)
    result = warehouse.query(registry.get("MX-001"), lo, hi, con=con)
    assert result.partial_months, "the last month should be reported as incomplete"


def test_counts_are_non_negative_everywhere(registry, con):
    lo, hi = warehouse.month_bounds(con)
    for m in registry.active:
        if m.unit != "count":
            continue
        for row in warehouse.query(m, lo, hi, con=con).rows:
            assert row["value"] is None or row["value"] >= 0


def test_the_stricter_active_is_never_larger_than_the_looser_one(registry, con):
    # MX-011 (took a terminal action) is a subset of MX-001 (did anything).
    # If that ever inverts, one of the two definitions has drifted.
    lo, hi = warehouse.month_bounds(con)
    loose_rows = warehouse.query(registry.get("MX-001"), lo, hi, con=con).rows
    strict_rows = warehouse.query(registry.get("MX-011"), lo, hi, con=con).rows
    loose = {r["month"]: r["value"] for r in loose_rows}
    strict = {r["month"]: r["value"] for r in strict_rows}
    for month, value in strict.items():
        assert value <= loose[month], month
