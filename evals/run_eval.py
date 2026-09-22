"""Does the thing actually behave, and how would we know it stopped.

Three questions, measured separately, because they fail for different reasons:

    routing       did it reach for the right metric
    refusal       did it decline the questions that have no answer here
    grounding     did every number it stated survive verification

Four drivers, selected with --llm:

    keyword        a deterministic word-overlap picker. No key, no network. It
                   is the baseline: whatever the model scores has to beat this
                   to have been worth the call.
    hallucinator   routes like the baseline, then states a number that is
                   nowhere in the data.
    misattributor  routes like the baseline, then states a REAL number from the
                   wrong month.
    anthropic      the model, given the same summaries the MCP server serves.

The last two exist so that grounding has cases it must fail on. A guard nobody
has watched fail is a guess — and the two stubs fail differently on purpose:
membership in the result catches the invented number every time and the
misattributed one never. That gap is what --check measures.

    python evals/run_eval.py --llm keyword
    python evals/run_eval.py --llm misattributor --check membership
    python evals/run_eval.py --llm anthropic --compare keyword
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from metrics_mcp import warehouse  # noqa: E402
from metrics_mcp.registry import Metric, load  # noqa: E402
from metrics_mcp.verify import check, check_result  # noqa: E402

CASES = Path(__file__).parent / "cases.yaml"
STOP = {
    "what",
    "is",
    "our",
    "the",
    "how",
    "many",
    "much",
    "in",
    "a",
    "of",
    "do",
    "we",
    "have",
    "did",
    "does",
    "was",
    "were",
    "and",
    "or",
    "to",
    "for",
    "per",
    "that",
    "than",
    "get",
    "getting",
    "make",
    "made",
    "come",
    "back",
    "take",
    "takes",
    "their",
    "first",
    "last",
    "this",
    "it",
    "its",
    "are",
    "be",
    "been",
    "on",
    "at",
    "by",
    "with",
    "from",
    "up",
}


@dataclass
class Outcome:
    question: str
    expected: str | None
    chosen: str | None
    answer: str
    grounded: bool | None
    unverified: list[str]

    @property
    def routed_right(self) -> bool:
        return self.chosen == self.expected


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP and len(w) > 2}


# ── drivers ───────────────────────────────────────────────────────────────


def keyword_driver(question: str, metrics: list[Metric]) -> str | None:
    """Word overlap against label plus definition. The floor to beat."""
    q = words(question)
    best, best_score = None, 0
    for m in metrics:
        score = len(q & words(f"{m.label} {m.definition}"))
        if score > best_score:
            best, best_score = m.id, score
    # Two words in common is not evidence; without a floor this picks something
    # for every question and never refuses.
    return best if best_score >= 2 else None


def hallucinator_driver(question: str, metrics: list[Metric]) -> str | None:
    """Routes like the baseline, then states a number that is nowhere in the data."""
    return keyword_driver(question, metrics)


def misattributor_driver(question: str, metrics: list[Metric]) -> str | None:
    """Routes like the baseline, then states a real number from the wrong month.

    The failure a membership check cannot see, and the reason verification is
    scoped to the month a sentence names.
    """
    return keyword_driver(question, metrics)


def anthropic_driver(question: str, metrics: list[Metric]) -> str | None:
    """Let the model pick, given the same summaries the MCP server serves."""
    import anthropic

    client = anthropic.Anthropic()
    catalogue = json.dumps([m.summary() for m in metrics], ensure_ascii=False)
    msg = client.messages.create(
        model=os.environ.get("EVAL_MODEL", "claude-sonnet-5"),
        max_tokens=200,
        system=(
            "You route a question to exactly one metric from a registry, or to nothing.\n"
            "Reply with the metric id alone, or the word NONE.\n"
            "NONE is the correct answer whenever no entry measures what was asked. "
            "A metric that is merely related is not an answer: picking it would "
            "report a different quantity under the name the person used."
        ),
        messages=[{"role": "user", "content": f"Registry:\n{catalogue}\n\nQuestion: {question}"}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    m = re.search(r"MX-\d{3}", text)
    return m.group(0) if m else None


DRIVERS = {
    "keyword": keyword_driver,
    "hallucinator": hallucinator_driver,
    "misattributor": misattributor_driver,
    "anthropic": anthropic_driver,
}


# ── harness ───────────────────────────────────────────────────────────────


def compose(metric: Metric, result: warehouse.Result, *, mode: str = "truth") -> str:
    """The sentence a driver ends up producing, given a result.

    Deliberately templated rather than generated: this harness measures
    routing, refusal and grounding, and a free-text generator would add a
    fourth source of variance to numbers meant to isolate the first three.

    Three modes, because the two ways of being wrong are not the same failure:

        truth         the value for the month it names
        invent        a number that exists nowhere in the data
        misattribute  a real number from a different month of the same series
    """
    rows = [r for r in result.rows if r["value"] is not None]
    if not rows:
        return f"No data for {metric.label} in that period."
    last = rows[-1]
    value = last["value"]

    if mode == "invent":
        value = round(float(value) * 1.11 + 7, 4)
    elif mode == "misattribute":
        other = next((r["value"] for r in reversed(rows[:-1]) if r["value"] != value), None)
        if other is None:
            return f"No data for {metric.label} in that period."
        value = other

    shown = f"{value:.1%}" if metric.unit == "rate" else f"{value:,.0f}"
    return f"{metric.label} in {last['month']} was {shown}."


MODES = {"hallucinator": "invent", "misattributor": "misattribute"}


def run(driver_name: str, con, *, scoped: bool = True) -> list[Outcome]:
    registry = load()
    metrics = registry.active
    driver = DRIVERS[driver_name]
    lo, hi = warehouse.month_bounds(con)
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))["cases"]

    outcomes: list[Outcome] = []
    for case in cases:
        chosen = driver(case["q"], metrics)
        if chosen is None:
            outcomes.append(
                Outcome(case["q"], case["metric"], None, "No metric covers that.", None, [])
            )
            continue

        metric = registry.get(chosen)
        result = warehouse.query(metric, lo, hi, con=con)
        answer = compose(metric, result, mode=MODES.get(driver_name, "truth"))
        # scoped: the claim is checked against the month it names. Unscoped is
        # plain membership in the series, kept so --check membership can show
        # what the narrower rule is actually buying.
        verdict = check_result(answer, result) if scoped else check(answer, result.numbers())
        outcomes.append(
            Outcome(case["q"], case["metric"], chosen, answer, verdict.ok, verdict.unverified)
        )
    return outcomes


def score(outcomes: list[Outcome]) -> dict:
    answerable = [o for o in outcomes if o.expected is not None]
    unanswerable = [o for o in outcomes if o.expected is None]
    grounded = [o for o in outcomes if o.grounded is not None]
    return {
        "n": len(outcomes),
        "routing": sum(o.routed_right for o in answerable) / max(len(answerable), 1),
        "refusal": sum(o.chosen is None for o in unanswerable) / max(len(unanswerable), 1),
        "false_refusal": sum(o.chosen is None for o in answerable) / max(len(answerable), 1),
        "grounding": sum(bool(o.grounded) for o in grounded) / max(len(grounded), 1),
    }


def report(name: str, s: dict) -> None:
    print(
        f"{name:14s} routing {s['routing']:6.1%}   "
        f"refusal {s['refusal']:6.1%}   "
        f"false refusal {s['false_refusal']:6.1%}   "
        f"grounded {s['grounding']:6.1%}   (n={s['n']})"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", default="keyword", choices=sorted(DRIVERS))
    ap.add_argument("--compare", action="append", default=[], choices=sorted(DRIVERS))
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument(
        "--check",
        default="scoped",
        choices=["scoped", "membership"],
        help="scoped checks a claim against the month it names; membership only "
        "asks whether the number appears anywhere in the series.",
    )
    args = ap.parse_args()

    con = warehouse.connect()
    try:
        results = {}
        for name in [args.llm, *args.compare]:
            if name in results:
                continue
            outcomes = run(name, con, scoped=(args.check == "scoped"))
            results[name] = score(outcomes)
            if args.verbose:
                print(f"\n── {name} " + "─" * 50)
                for o in outcomes:
                    mark = "ok  " if o.routed_right else "MISS"
                    ground = "" if o.grounded is not False else f"  [UNVERIFIED {o.unverified}]"
                    left = f"{o.expected or 'NONE':7s} -> {o.chosen or 'NONE':7s}"
                    print(f"  {mark} {left}  {o.question}{ground}")
                print()
    finally:
        con.close()

    print()
    for name, s in results.items():
        report(name, s)

    # The hallucinator must fail grounding. If it ever passes, the check has
    # stopped checking and every other number in this report is unsupported.
    if "hallucinator" in results and results["hallucinator"]["grounding"] > 0:
        print("\nFAIL: the hallucinator's numbers passed verification", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
