"""Checking that every number in a sentence came from the result it claims.

The promise "only use the numbers you were given" is a prompt, and a prompt is
not a guarantee. This module is the guarantee: it pulls every numeral out of a
piece of prose and asks whether each one is in the allowed set. A sentence with
a number nobody can account for does not ship.

Membership in the result is the floor, not the ceiling. Checking only that a
number appears *somewhere* in the result lets the worst realistic error
through: a figure lifted from the wrong month. It is a real number, it is in
the data, and it is attached to the wrong claim. So when a sentence names a
month the result contains, the allowed set narrows to that month's row —
`check_result` does this, and the eval measures the difference (a stub that
states plausible-but-wrong numbers is caught 40% of the time by membership
alone, and every time once the claim is scoped to the month it names).

Two things it still does not do.

It does not check meaning. "Activation fell" alongside a correct, rising number
is a sentence this module will pass. It narrows failure to the numeric kind,
which is the kind that destroys trust fastest and the only kind a machine can
settle on its own.

It does not try to be clever about rounding. Every comparison happens at the
precision the number was printed with, so "12.3" matches a stored 12.34 and
"1,240" matches 1240. Anything looser would start accepting numbers that are
merely nearby.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Numerals as prose writes them: 1,240 · 12.3 · 45% · -3.
#
# The grouped form is listed first and requires at least one separator group,
# so that the plain form below cannot match the leading three digits of a
# longer number and report "202" out of "2026". Both forms refuse to start or
# end inside a longer numeric run.
# The trailing lookahead is `(?!\d)` and not `(?![\d.])`: a number at the end
# of a sentence is followed by a full stop, and excluding a following dot made
# the whole match fail and silently skip it. A verifier that cannot see the
# last number in a sentence is not a verifier — there is a test for exactly
# this.
NUMBER_RE = re.compile(
    r"(?<![\d.,])-?\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?%?"
    r"|(?<![\d.,])-?\d+(?:\.\d+)?%?(?!\d)"
)

# A month or a date is a label, not a measurement, and it is the one thing a
# sentence about a time series is guaranteed to contain. Masked before the scan
# rather than allow-listed afterwards, so "2026-09" cannot be smuggled in as
# evidence for the number 2026.
DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")


@dataclass
class Check:
    ok: bool
    unverified: list[str]
    found: list[str]

    def reason(self) -> str:
        if self.ok:
            return "every number in the text appears in the result"
        return "not in the result: " + ", ".join(self.unverified)


def _parse(token: str) -> tuple[float, int]:
    """Return the value and how many decimal places it was written with."""
    pct = token.endswith("%")
    raw = token.rstrip("%").replace(",", "").replace(" ", "")
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    value = float(raw)
    if pct:
        value /= 100.0
    return value, decimals


def allowed_tokens(values: set[float]) -> set[float]:
    """The values a text may mention, including their percentage readings.

    A rate of 0.238 is legitimately written as "23.8%", so both readings are
    permitted for the same underlying number.
    """
    out: set[float] = set()
    for v in values:
        out.add(v)
        out.add(v * 100.0)
    return out


def check(text: str, values: set[float], *, extra: set[float] | None = None) -> Check:
    """Verify `text` against the numbers in a result.

    `extra` is for numbers that are legitimately not measurements — the count
    of months in a window, for instance, which the caller knows and states.
    """
    permitted = allowed_tokens(values) | allowed_tokens(extra or set())
    found, unverified = [], []
    scanned = DATE_RE.sub(" ", text)

    for token in NUMBER_RE.findall(scanned):
        found.append(token)
        value, decimals = _parse(token)
        # Compare at the precision the text used: a printed 12.3 is allowed to
        # stand for a stored 12.34, but 12.5 is not.
        if any(round(p, decimals) == round(value, decimals) for p in permitted):
            continue
        # A percentage may have been written from the rate, or the other way
        # round; both readings were admitted above, so anything left is absent.
        unverified.append(token)

    return Check(ok=not unverified, unverified=unverified, found=found)


MONTH_RE = re.compile(r"\b(\d{4}-\d{2})\b")


def check_result(text: str, result) -> Check:
    """Verify `text` against a result, scoped to the months the text names.

    A claim that names a month is a claim about that month. Widening the
    allowed set to the whole series would accept a real number attached to the
    wrong period — the failure that survives a membership check and is hardest
    to spot by eye, because nothing about the sentence looks invented.

    When the text names no month, the claim is about the series as a whole and
    every value in it is fair game.
    """
    named = [m for m in MONTH_RE.findall(text) if any(r.get("month") == m for r in result.rows)]

    if named:
        values: set[float] = set()
        for row in result.rows:
            if row.get("month") not in named:
                continue
            for key in ("value", "numerator", "denominator"):
                v = row.get(key)
                if isinstance(v, (int, float)):
                    values.add(float(v))
    else:
        values = result.numbers()

    return check(text, values)
