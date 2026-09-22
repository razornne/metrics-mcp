"""Command line: build the thing, check it, query it, serve it."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import warehouse
from .registry import load, validate_against_warehouse

app = typer.Typer(add_completion=False, help="A metric registry an agent has to go through.")
console = Console()
ROOT = Path(__file__).resolve().parents[2]


def _run(cmd: list[str], **kw) -> int:
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    return subprocess.call(cmd, cwd=ROOT, **kw)


@app.command()
def seed() -> None:
    """Generate the synthetic dataset (deterministic, from a fixed seed)."""
    raise typer.Exit(_run([sys.executable, "data/generate.py"]))


@app.command()
def build(skip_seed: bool = typer.Option(False, help="Assume data/raw already exists.")) -> None:
    """Generate the data and build the dbt warehouse."""
    if not skip_seed:
        if code := _run([sys.executable, "data/generate.py"]):
            raise typer.Exit(code)
    env = {"DBT_PROFILES_DIR": "warehouse"}
    import os

    code = _run(
        [sys.executable, "-m", "dbt.cli.main", "build", "--project-dir", "warehouse"],
        env=os.environ | env,
    )
    raise typer.Exit(code)


@app.command()
def check() -> None:
    """Resolve every registry entry against the built warehouse.

    This is the guard that makes the registry worth trusting: a metric whose
    column was renamed fails here, in CI, instead of returning a wrong number
    to whoever asked next.
    """
    registry = load()
    con = warehouse.connect()
    try:
        problems = validate_against_warehouse(registry, con)
    finally:
        con.close()

    if problems:
        for p in problems:
            console.print(f"[red]FAIL[/red] {p}")
        console.print(f"\n[red]{len(problems)} problem(s)[/red]")
        raise typer.Exit(1)

    n_cols = sum(len(m.columns) for m in registry.metrics)
    console.print(
        f"[green]OK[/green] {len(registry.metrics)} metrics resolve against the warehouse "
        f"({n_cols} column references checked)"
    )


@app.command("list")
def list_metrics() -> None:
    """Show the registry."""
    registry = load()
    table = Table(show_lines=False)
    for col in ("id", "label", "unit", "model", "dimensions", "owner"):
        table.add_column(col)
    for m in registry.metrics:
        table.add_row(m.id, m.label, m.unit, m.model, ", ".join(m.dimensions) or "-", m.owner)
    console.print(table)


@app.command()
def query(
    metric_id: str,
    start: str = typer.Option(None, "--start", help="YYYY-MM, defaults to the first month."),
    end: str = typer.Option(None, "--end", help="YYYY-MM, defaults to the last."),
    dimension: str = typer.Option(None, "--by", help="One of the metric's allowed dimensions."),
    show_sql: bool = typer.Option(False, "--sql", help="Print the generated statement."),
) -> None:
    """Compute a metric, the same way the MCP server does."""
    registry = load()
    try:
        m = registry.get(metric_id)
    except KeyError:
        console.print(f"[red]no metric {metric_id!r}[/red]")
        raise typer.Exit(1) from None

    con = warehouse.connect()
    try:
        lo, hi = warehouse.month_bounds(con)
        start_d = date(*(int(x) for x in start.split("-")), 1) if start else lo
        end_d = date(*(int(x) for x in end.split("-")), 1) if end else hi
        result = warehouse.query(m, start_d, end_d, dimension, con=con)
    except warehouse.QueryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    finally:
        con.close()

    console.print(f"[bold]{m.id} - {m.label}[/bold]  [dim]{m.unit}[/dim]")
    table = Table()
    for col in result.rows[0] if result.rows else ["month", "value"]:
        table.add_column(col)
    for row in result.rows:
        table.add_row(*("-" if v is None else str(v) for v in row.values()))
    console.print(table)

    if result.partial_months:
        console.print(
            f"[yellow]{', '.join(result.partial_months)} is incomplete - "
            "not comparable with the months before it.[/yellow]"
        )
    if show_sql:
        console.print(f"\n[dim]{result.sql}[/dim]")


@app.command()
def serve() -> None:
    """Run the MCP server on stdio."""
    from .server import main

    main()


if __name__ == "__main__":
    app()
