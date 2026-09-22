"""The registry is only worth trusting if bad entries cannot load."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from metrics_mcp.registry import Metric, MetricError, Registry, load

BASE = {
    "id": "T-001",
    "label": "Test metric",
    "definition": "Something.",
    "grain": "month",
    "unit": "count",
    "model": "fct_account_month",
}


def make(**over) -> Metric:
    return Metric(**(BASE | over))


def test_the_shipped_registry_loads():
    registry = load()
    assert len(registry.metrics) >= 10
    assert all(m.definition.strip() for m in registry.metrics)


def test_count_needs_a_measure():
    with pytest.raises((MetricError, ValidationError)):
        make(type="count_distinct")


def test_ratio_needs_both_halves():
    with pytest.raises((MetricError, ValidationError)):
        make(type="ratio", numerator="is_retained")


def test_ratio_cannot_also_have_a_measure():
    with pytest.raises((MetricError, ValidationError)):
        make(type="ratio", numerator="a", denominator="b", measure="c")


@pytest.mark.parametrize(
    "bad",
    [
        "1=1 or true",
        "account_id; drop table fct_account_month",
        "(select 1)",
        "is_active and 1=1",
        "plan = 'free'",
    ],
)
def test_filter_rejects_anything_beyond_a_simple_predicate(bad):
    # The filter string is written into SQL, so the grammar it accepts is the
    # security boundary. A column compared to a number is the whole language.
    with pytest.raises((MetricError, ValidationError)):
        make(type="count_distinct", measure="account_id", filter=bad)


@pytest.mark.parametrize("good", ["is_active", "n_terminal_events > 0", "mrr >= 1"])
def test_filter_accepts_the_two_shapes_it_is_meant_to(good):
    assert make(type="count_distinct", measure="account_id", filter=good).filter == good


def test_identifiers_must_be_plain():
    with pytest.raises((MetricError, ValidationError)):
        make(type="sum", measure="sum(mrr) from other_table --")


def test_duplicate_label_is_rejected():
    # Two entries with the same human name make "which metric do you mean"
    # unanswerable for an agent picking by label.
    a = make(type="sum", measure="mrr")
    b = make(id="T-002", type="sum", measure="mrr")
    with pytest.raises((MetricError, ValidationError)):
        Registry(version=1, metrics=[a, b])


def test_lookup_by_id_or_label():
    registry = load()
    assert registry.get("MX-001").id == "MX-001"
    assert registry.get("active accounts").id == "MX-001"
    with pytest.raises(KeyError):
        registry.get("engagement score")


def test_columns_include_the_filter_column():
    m = make(type="count_distinct", measure="account_id", filter="n_terminal_events > 0")
    assert "n_terminal_events" in m.columns
