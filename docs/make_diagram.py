"""Render the architecture diagram, light and dark, from one definition.

    python docs/make_diagram.py

GitHub picks the file by the reader's theme via <picture> in the README, so
two files have to exist — and two hand-drawn files drift. This is the one
source; both outputs are generated and committed.

Colours are GitHub's own canvas palette rather than anything of mine, so the
diagram sits on the README as if it belonged there instead of as a pasted
image with its own background.
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).parent

THEMES = {
    "light": {
        "bg": "#ffffff",
        "box": "#f6f8fa",
        "rule": "#d1d9e0",
        "ink": "#1f2328",
        "muted": "#59636e",
        "accent": "#0969da",
        "accent_soft": "#ddf4ff",
    },
    "dark": {
        "bg": "#0d1117",
        "box": "#161b22",
        "rule": "#30363d",
        "ink": "#e6edf3",
        "muted": "#9198a1",
        "accent": "#4493f8",
        "accent_soft": "#121d2f",
    },
}

MONO = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace"
SANS = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

W, H = 760, 560

# title, subtitle lines, x, y, w, h, highlighted
BOXES = [
    ("data/generate.py", ["seeded · offline"], 40, 40, 230, 62, False),
    ("dbt on DuckDB", ["7 models · 29 tests"], 330, 40, 250, 62, False),
    (
        "metrics/_catalog.yml",
        ["11 metrics", "definition · grain · caveats"],
        40,
        170,
        250,
        78,
        True,
    ),
    (
        "registry + SQL builder",
        ["every identifier comes", "from the registry"],
        350,
        170,
        250,
        78,
        False,
    ),
    ("MCP server", ["4 tools · no run_sql"], 250, 320, 200, 62, True),
    ("mx CLI", ["same query path"], 500, 320, 180, 62, False),
    (
        "verify_answer",
        ["every number checked against", "the result it cites"],
        180,
        450,
        340,
        70,
        True,
    ),
]

# x1, y1, x2, y2
ARROWS = [
    (270, 71, 322, 71),  # generate -> dbt
    (455, 102, 455, 162),  # dbt -> builder
    (290, 209, 342, 209),  # registry -> builder
    (475, 248, 475, 280),  # builder -> split bus
    (350, 288, 350, 312),  # bus -> MCP
    (590, 288, 590, 312),  # bus -> CLI
    (350, 382, 350, 442),  # MCP -> verify
]

# The horizontal bus that splits the builder into its two consumers.
BUS = (350, 284, 590, 284)

NOTES = [
    ("mx check — 43 column refs resolved,", 40, 274, MONO, 10.5),
    ("or the build fails", 40, 289, MONO, 10.5),
]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(theme: str) -> str:
    c = THEMES[theme]
    p: list[str] = []
    p.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="metrics-mcp architecture: generated data and dbt build a DuckDB '
        f"warehouse; a YAML registry and a SQL builder sit between it and the two "
        f"consumers, an MCP server with four tools and a CLI; answers pass through "
        f'verify_answer.">'
    )
    p.append(
        f'<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{c["muted"]}"/></marker></defs>'
    )
    p.append(f'<rect width="{W}" height="{H}" fill="{c["bg"]}"/>')

    # Bus first, so boxes draw over its ends.
    x1, y1, x2, _ = BUS
    p.append(f'<path d="M{x1},{y1} H{x2}" stroke="{c["muted"]}" stroke-width="1.5" fill="none"/>')

    for ax1, ay1, ax2, ay2 in ARROWS:
        p.append(
            f'<path d="M{ax1},{ay1} L{ax2},{ay2}" stroke="{c["muted"]}" '
            f'stroke-width="1.5" fill="none" marker-end="url(#a)"/>'
        )

    for title, subs, x, y, w, h, hot in BOXES:
        fill = c["accent_soft"] if hot else c["box"]
        stroke = c["accent"] if hot else c["rule"]
        p.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
        )
        p.append(
            f'<text x="{x + 14}" y="{y + 25}" font-family="{SANS}" font-size="13.5" '
            f'font-weight="600" fill="{c["ink"]}">{esc(title)}</text>'
        )
        for i, sub in enumerate(subs):
            p.append(
                f'<text x="{x + 14}" y="{y + 44 + i * 15}" font-family="{MONO}" '
                f'font-size="10.5" fill="{c["muted"]}">{esc(sub)}</text>'
            )

    for text, x, y, font, size in NOTES:
        p.append(
            f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" '
            f'fill="{c["muted"]}">{esc(text)}</text>'
        )

    p.append("</svg>\n")
    return "\n".join(p)


def main() -> None:
    for theme in THEMES:
        path = OUT / f"architecture-{theme}.svg"
        path.write_text(render(theme), encoding="utf-8")
        print(f"wrote {path.relative_to(OUT.parent)}")


if __name__ == "__main__":
    main()
