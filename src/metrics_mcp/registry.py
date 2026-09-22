"""Loading and validating the metric registry.

The registry is data, and everything downstream treats it as the only source
of a definition. That is only safe if the file is checked against the
warehouse rather than trusted, which is what `validate_against_warehouse` does
and what `mx check` runs in CI.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

REGISTRY_DIR = Path(__file__).resolve().parents[2] / "metrics"

# A filter is written into SQL, so it is not free text. It may be a bare column
# name or a column compared to a number — enough for `is_active` and
# `n_terminal_events > 0`, and not enough to smuggle a subquery through the
# registry file.
FILTER_RE = re.compile(r"^[a-z_][a-z0-9_]*(\s*(=|!=|>|<|>=|<=)\s*-?\d+(\.\d+)?)?$")
IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


class MetricError(ValueError):
    """A registry entry that cannot be trusted to build SQL from."""


class Metric(BaseModel):
    id: str
    label: str
    definition: str
    grain: Literal["month"]
    unit: Literal["count", "currency", "rate", "ratio"]
    model: str
    type: Literal["count_distinct", "sum", "ratio"]
    measure: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    filter: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    owner: str = "analytics"
    status: Literal["active", "deprecated", "draft"] = "active"
    caveats: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Metric:
        if self.type == "ratio":
            if not (self.numerator and self.denominator):
                raise MetricError(f"{self.id}: a ratio needs numerator and denominator")
            if self.measure:
                raise MetricError(f"{self.id}: a ratio has no single measure")
        else:
            if not self.measure:
                raise MetricError(f"{self.id}: {self.type} needs a measure")
            if self.numerator or self.denominator:
                raise MetricError(f"{self.id}: only a ratio has numerator/denominator")

        for name in (self.measure, self.numerator, self.denominator, self.model, *self.dimensions):
            if name is not None and not IDENT_RE.match(name):
                raise MetricError(f"{self.id}: {name!r} is not a plain identifier")
        if self.filter and not FILTER_RE.match(self.filter):
            raise MetricError(f"{self.id}: filter {self.filter!r} is not a simple predicate")
        return self

    @property
    def columns(self) -> list[str]:
        """Every warehouse column this metric depends on."""
        cols = [c for c in (self.measure, self.numerator, self.denominator) if c]
        cols += self.dimensions
        if self.filter:
            cols.append(self.filter.split()[0])
        return sorted(set(cols))

    def summary(self) -> dict:
        """What the agent is given. Caveats travel with the definition."""
        out = {
            "id": self.id,
            "label": self.label,
            "definition": " ".join(self.definition.split()),
            "grain": self.grain,
            "unit": self.unit,
            "dimensions": self.dimensions,
            "owner": self.owner,
            "status": self.status,
        }
        if self.caveats:
            out["caveats"] = " ".join(self.caveats.split())
        return out


class Registry(BaseModel):
    version: int
    metrics: list[Metric]

    @model_validator(mode="after")
    def _unique(self) -> Registry:
        for field in ("id", "label"):
            seen: dict[str, str] = {}
            for m in self.metrics:
                key = getattr(m, field).lower()
                if key in seen:
                    # A duplicate label is as bad as a duplicate id: the agent
                    # is asked to pick by name, and two entries called the same
                    # thing make that choice unanswerable.
                    raise MetricError(
                        f"duplicate {field} {getattr(m, field)!r}: {seen[key]} and {m.id}"
                    )
                seen[key] = m.id
        return self

    def get(self, metric_id: str) -> Metric:
        want = metric_id.strip().lower()
        for m in self.metrics:
            if m.id.lower() == want or m.label.lower() == want:
                return m
        raise KeyError(metric_id)

    @property
    def active(self) -> list[Metric]:
        return [m for m in self.metrics if m.status == "active"]


def load(directory: Path | None = None) -> Registry:
    directory = directory or REGISTRY_DIR
    metrics: list[dict] = []
    version = 1
    for path in sorted(directory.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        version = doc.get("version", version)
        metrics.extend(doc.get("metrics", []))
    if not metrics:
        raise MetricError(f"no metrics found in {directory}")
    return Registry(version=version, metrics=metrics)


def validate_against_warehouse(registry: Registry, con) -> list[str]:
    """Resolve every model and column against the built warehouse.

    Returns the problems rather than raising, so `mx check` can print all of
    them at once instead of one per run.
    """
    problems: list[str] = []
    rows = con.execute("select table_name, column_name from information_schema.columns").fetchall()
    schema: dict[str, set[str]] = {}
    for table, column in rows:
        schema.setdefault(table, set()).add(column)

    for m in registry.metrics:
        if m.model not in schema:
            problems.append(f"{m.id}: model {m.model!r} is not in the warehouse")
            continue
        if "month" not in schema[m.model]:
            problems.append(f"{m.id}: model {m.model!r} has no month column")
        for column in m.columns:
            if column not in schema[m.model]:
                problems.append(f"{m.id}: {m.model}.{column} does not exist")
    return problems
