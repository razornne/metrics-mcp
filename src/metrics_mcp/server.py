"""The MCP server.

Four tools, and the shape of them is the argument this repo is making:

    list_metrics    what exists
    get_metric      what one of them means, caveats included
    query_metric    the numbers, with the SQL that produced them
    verify_answer   whether a sentence is allowed to be sent

There is no `run_sql`. An agent that can write its own SQL can also invent its
own definition of "active user", and then two answers in the same conversation
quietly mean different things. Going through the registry costs a round trip
and buys the guarantee that a metric means one thing.

`verify_answer` is a tool rather than a post-processing step on purpose: the
model can be told to call it before answering, and a client that ignores it
still leaves the check available to whatever wraps it.
"""

from __future__ import annotations

import json
from datetime import date

from mcp.server.mcpserver import MCPServer

from . import warehouse
from .registry import Registry, load
from .verify import check_result

mcp = MCPServer("metrics-mcp")

_registry: Registry | None = None
_results: dict[str, warehouse.Result] = {}


def registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = load()
    return _registry


def _parse_month(value: str, *, end: bool) -> date:
    """Accept '2026-03'. A day would imply the data is finer than it is."""
    try:
        year, month = (int(p) for p in value.split("-")[:2])
        return date(year, month, 1)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{value!r} is not a month like '2026-03'") from exc


@mcp.tool()
def list_metrics(owner: str | None = None) -> str:
    """List the metrics this warehouse can answer for.

    Returns id, label, a one-line definition and the dimensions each may be cut
    by. Start here: a metric that is not on this list cannot be computed, and
    the honest answer to a question about it is that it does not exist yet.
    """
    metrics = registry().active
    if owner:
        metrics = [m for m in metrics if m.owner == owner.strip().lower()]
    return json.dumps([m.summary() for m in metrics], ensure_ascii=False, indent=2)


@mcp.tool()
def get_metric(metric_id: str) -> str:
    """The full definition of one metric, including its caveats.

    Read the caveats before describing a number to anyone. They are where the
    reasons a figure is easy to misread are written down — a denominator that
    is not what a reader would assume, a most-recent month that is not yet
    complete.
    """
    try:
        m = registry().get(metric_id)
    except KeyError:
        known = ", ".join(x.id for x in registry().active)
        return json.dumps({"error": f"no metric {metric_id!r}. Known: {known}"})
    payload = m.summary() | {"model": m.model, "type": m.type}
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def query_metric(
    metric_id: str,
    start_month: str,
    end_month: str,
    dimension: str | None = None,
) -> str:
    """Compute a metric over a range of months, optionally cut by one dimension.

    Months are 'YYYY-MM' and both ends are inclusive. `dimension` must be one
    of the values listed for the metric; anything else is refused rather than
    approximated.

    The reply carries a `result_id`. Pass it to verify_answer together with
    whatever you are about to say, and say nothing whose numbers it rejects.
    Ratios come back with their numerator and denominator so a wider period can
    be rolled up by summing those, never by averaging the rates.
    """
    try:
        m = registry().get(metric_id)
    except KeyError:
        known = ", ".join(x.id for x in registry().active)
        return json.dumps({"error": f"no metric {metric_id!r}. Known: {known}"})

    try:
        start = _parse_month(start_month, end=False)
        end = _parse_month(end_month, end=True)
        result = warehouse.query(m, start, end, dimension)
    except (ValueError, warehouse.QueryError) as exc:
        return json.dumps({"error": str(exc)})

    result_id = f"res_{len(_results) + 1:04d}"
    _results[result_id] = result

    payload = result.to_payload() | {"result_id": result_id}
    if result.partial_months:
        payload["warning"] = (
            f"{', '.join(result.partial_months)} is not a complete month; "
            "do not compare it with the months before it."
        )
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
def verify_answer(result_id: str, text: str) -> str:
    """Check that every number in `text` came from the result it cites.

    Call this on the answer you intend to give, before giving it. If it comes
    back with unverified numbers, the answer is wrong somewhere: fix it or say
    less. Do not restate a rejected number with different wording.
    """
    result = _results.get(result_id)
    if result is None:
        return json.dumps(
            {"error": f"no result {result_id!r}. Call query_metric first and use its result_id."}
        )
    outcome = check_result(text, result)
    return json.dumps(
        {
            "ok": outcome.ok,
            "unverified": outcome.unverified,
            "numbers_in_text": outcome.found,
            "reason": outcome.reason(),
        },
        ensure_ascii=False,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
