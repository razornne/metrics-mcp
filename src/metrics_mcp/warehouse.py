"""Turning a registry entry into SQL, and running it.

The agent never supplies SQL, a table name, or a column name. It supplies a
metric id, a period and optionally one dimension — and the dimension has to be
on that metric's allow-list. Every identifier in the statement below comes from
the registry file, which has itself been checked against the warehouse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb

from .registry import Metric

DB_PATH = Path(__file__).resolve().parents[2] / "warehouse.duckdb"


class QueryError(ValueError):
    """A request the registry does not allow."""


@dataclass
class Result:
    metric: Metric
    dimension: str | None
    rows: list[dict]
    sql: str
    partial_months: list[str] = field(default_factory=list)

    def numbers(self) -> set[float]:
        """Every value a sentence about this result is allowed to contain."""
        out: set[float] = set()
        for row in self.rows:
            v = row.get("value")
            if isinstance(v, (int, float)):
                out.add(float(v))
        return out

    def to_payload(self) -> dict:
        return {
            "metric": self.metric.summary(),
            "dimension": self.dimension,
            "rows": self.rows,
            "partial_months": self.partial_months,
            "sql": self.sql,
        }


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    target = path or DB_PATH
    if not target.exists():
        raise QueryError(
            f"no warehouse at {target}. Run `mx build` first — it generates the data and "
            "builds the dbt models."
        )
    return duckdb.connect(str(target), read_only=True)


def build_sql(metric: Metric, dimension: str | None) -> str:
    if dimension is not None and dimension not in metric.dimensions:
        raise QueryError(
            f"{metric.id} cannot be cut by {dimension!r}. "
            f"Allowed: {', '.join(metric.dimensions) or 'none'}."
        )

    if metric.type == "count_distinct":
        value = f"count(distinct {metric.measure})"
        parts = ""
    elif metric.type == "sum":
        value = f"sum({metric.measure})"
        parts = ""
    else:
        # Ratio: the components are returned alongside the value, so a reader
        # can see what it was divided by, and so a roll-up to a wider period
        # can be done correctly by summing them rather than averaging rates.
        num = f"sum(cast({metric.numerator} as double))"
        den = f"sum(cast({metric.denominator} as double))"
        value = f"case when {den} = 0 then null else {num} / {den} end"
        parts = f",\n    {num} as numerator,\n    {den} as denominator"

    group_dim = f",\n    {dimension}" if dimension else ""
    order_dim = f", {dimension}" if dimension else ""

    # The period predicate always applies; the registry filter, when present,
    # is ANDed onto it rather than replacing it.
    predicate = "month between ? and ?"
    if metric.filter:
        predicate += f" and {metric.filter}"
    group_by = "1" + (", 2" if dimension else "")

    return (
        f"select\n"
        f"    strftime(month, '%Y-%m') as month{group_dim},\n"
        f"    {value} as value{parts},\n"
        f"    max(is_partial) as is_partial\n"
        f"from {metric.model}\n"
        f"where {predicate}\n"
        f"group by {group_by}\n"
        f"order by month{order_dim}"
    )


def query(
    metric: Metric,
    start: date,
    end: date,
    dimension: str | None = None,
    con: duckdb.DuckDBPyConnection | None = None,
) -> Result:
    owned = con is None
    con = con or connect()
    try:
        sql = build_sql(metric, dimension)
        cur = con.execute(sql, [start, end])
        names = [d[0] for d in cur.description]
        raw = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    finally:
        if owned:
            con.close()

    partial = [r["month"] for r in raw if r.get("is_partial")]
    rows = []
    for r in raw:
        row = {k: v for k, v in r.items() if k != "is_partial"}
        if isinstance(row.get("value"), float):
            row["value"] = round(row["value"], 4)
        for k in ("numerator", "denominator"):
            if isinstance(row.get(k), float):
                row[k] = round(row[k], 4)
        rows.append(row)

    return Result(metric=metric, dimension=dimension, rows=rows, sql=sql, partial_months=partial)


def month_bounds(con: duckdb.DuckDBPyConnection) -> tuple[date, date]:
    lo, hi = con.execute("select min(month), max(month) from dim_month").fetchone()
    return lo, hi
