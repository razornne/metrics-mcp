"""The tool surface, called the way a client calls it."""

from __future__ import annotations

import json

import pytest

from metrics_mcp import server, warehouse

pytestmark = pytest.mark.skipif(
    not warehouse.DB_PATH.exists(), reason="warehouse not built; run `mx build`"
)


def call(tool, **kwargs):
    """Invoke a tool's implementation directly.

    The SDK's decorator returns the plain function in 2.x and a wrapper with
    `.fn` in 1.x; accept either so the suite is not pinned to one of them.
    """
    fn = getattr(tool, "fn", tool)
    return json.loads(fn(**kwargs))


def test_list_metrics_returns_definitions_and_dimensions():
    out = call(server.list_metrics)
    assert len(out) >= 10
    assert all({"id", "label", "definition", "dimensions"} <= set(m) for m in out)


def test_list_metrics_filters_by_owner():
    assert {m["owner"] for m in call(server.list_metrics, owner="finance")} == {"finance"}


def test_get_metric_carries_the_caveats():
    out = call(server.get_metric, metric_id="MX-007")
    assert "terminal" in out["caveats"].lower()


def test_an_unknown_metric_is_an_error_that_lists_the_known_ones():
    out = call(server.get_metric, metric_id="MX-999")
    assert "error" in out and "MX-001" in out["error"]


def test_query_returns_rows_and_the_sql_that_made_them():
    out = call(server.query_metric, metric_id="MX-001", start_month="2026-01", end_month="2026-03")
    assert len(out["rows"]) == 3
    assert out["sql"].startswith("select")
    assert out["result_id"].startswith("res_")


def test_a_dimension_outside_the_allow_list_is_refused_not_guessed():
    out = call(
        server.query_metric,
        metric_id="MX-001",
        start_month="2026-01",
        end_month="2026-03",
        dimension="account_id",
    )
    assert "error" in out and "cannot be cut by" in out["error"]


def test_a_malformed_month_is_an_error():
    out = call(server.query_metric, metric_id="MX-001", start_month="March", end_month="2026-03")
    assert "error" in out


def test_the_incomplete_month_comes_back_with_a_warning():
    con = warehouse.connect()
    try:
        lo, hi = warehouse.month_bounds(con)
    finally:
        con.close()
    out = call(
        server.query_metric,
        metric_id="MX-001",
        start_month=lo.strftime("%Y-%m"),
        end_month=hi.strftime("%Y-%m"),
    )
    assert "warning" in out and "not a complete month" in out["warning"]


def test_verify_accepts_a_number_from_the_result():
    q = call(server.query_metric, metric_id="MX-001", start_month="2026-01", end_month="2026-03")
    row = q["rows"][0]
    text = f"Active accounts in {row['month']} were {row['value']}."
    out = call(server.verify_answer, result_id=q["result_id"], text=text)
    assert out["ok"], out


def test_verify_rejects_a_number_borrowed_from_another_month():
    # The failure a membership check cannot see: a real number, wrong month.
    q = call(server.query_metric, metric_id="MX-001", start_month="2026-01", end_month="2026-06")
    rows = [r for r in q["rows"] if r["value"] is not None]
    a, b = rows[0], next(r for r in rows[1:] if r["value"] != rows[0]["value"])
    text = f"Active accounts in {a['month']} were {b['value']}."
    out = call(server.verify_answer, result_id=q["result_id"], text=text)
    assert not out["ok"]
    assert str(b["value"]) in " ".join(out["unverified"])


def test_verify_rejects_an_invented_number():
    q = call(server.query_metric, metric_id="MX-001", start_month="2026-01", end_month="2026-03")
    out = call(server.verify_answer, result_id=q["result_id"], text="It reached 999,999.")
    assert not out["ok"] and out["unverified"] == ["999,999"]


def test_verify_without_a_query_first_says_so():
    out = call(server.verify_answer, result_id="res_nope", text="1")
    assert "error" in out


def test_the_exposed_tools_are_exactly_the_four():
    # The whole argument of the repo is the absence of a raw-SQL tool. If one
    # is ever added, the registry stops being the only definition of a metric,
    # and this test is the thing that notices.
    import asyncio

    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == {"list_metrics", "get_metric", "query_metric", "verify_answer"}
