"""The palette for DeltaTrack's report and its examples landing page.

Two surfaces take their values from here: the report (`formatters/diff_html.py`), and the
published examples landing page (`scripts/render_examples.py`), which embeds a subset.
Nothing else does yet. The web app declares its own values in `webapp/css/styles.css`,
and the loading tab in `webapp/js/compare.js` hardcodes four of them, so editing this
module reaches neither and no test would report the divergence. Consolidating those is
separate, later work.

Both surfaces *embed* rather than link, and any surface wired up later will have to as
well. A report has to render with no network at all (ADR 0011), so it cannot fetch a
stylesheet, and the loading tab is written via `document.write` and can resolve no URLs.
"One palette" can therefore only mean one source generated into each surface, never one
file they all link.

Keep the set to what the stylesheets actually use. An unreferenced token ships in every
report and styles nothing, which is how eleven of them accumulated with the whole suite
green (#667). `tests/test_committed_examples.py` gates that against a rendered report.

The four diff states (added, removed, modified, moved) are the vocabulary bill
comparison needs, and each carries a background and a foreground.

History: #667 took ownership of these values; #676 is the epic that made DeltaTrack's UI
its own.
"""

from __future__ import annotations

# Ordered by role, and the order is what a reader sees in the emitted `:root`.
PALETTE: dict[str, str] = {
    # Surfaces and text
    "--background": "#f9f7f5",
    "--foreground": "#1c1c3a",
    "--card": "#ffffff",
    "--border": "#e3ddd7",
    # Brand
    "--primary": "#2c2c5c",
    "--primary-foreground": "#f9f7f5",
    "--secondary": "#eef0f8",
    "--muted": "#f2f0ed",
    "--muted-foreground": "#686881",
    "--accent": "#ede8df",
    "--gold": "#c9944e",
    # Status
    "--destructive": "#c04040",
    "--success": "#3d9b6d",
    # Information notices (the financial views' "About these figures" alert, ADR 0023)
    "--info": "#e3f2f8",
    "--info-foreground": "#0b4f63",
    # Diff states, one background/foreground pair each
    "--diff-add": "#d3f0e2",
    "--diff-add-foreground": "#1a6647",
    "--diff-remove": "#f5ddd8",
    "--diff-remove-foreground": "#8a2828",
    "--diff-modified": "#f1e6d2",
    "--diff-modified-foreground": "#8a6320",
    "--diff-moved": "#eef0f8",
    "--diff-moved-foreground": "#2c2c5c",
    # Money types in the financial views (ADR 0023), for the types no diff state already
    # colours: appropriation takes --diff-add, rescission and restriction --diff-remove, cap
    # --diff-modified, unknown --muted. Values are the research notebook's report's.
    "--fin-transfer": "#cffafe",
    "--fin-transfer-foreground": "#155e75",
    "--fin-authorization": "#e0f2fe",
    "--fin-authorization-foreground": "#0c4a6e",
    "--fin-fee": "#ffedd5",
    "--fin-fee-foreground": "#9a3412",
    "--fin-directive": "#e0e7ff",
    "--fin-directive-foreground": "#3730a3",
    "--fin-earmark": "#ede9fe",
    "--fin-earmark-foreground": "#5b21b6",
    "--fin-availability": "#d1fae5",
    "--fin-availability-foreground": "#065f46",
    "--fin-sub-allocation": "#dbeafe",
    "--fin-sub-allocation-foreground": "#1e40af",
    # Geometry
    "--radius": "0.625rem",
    # How far a jump target clears the report's sticky action bar. 64px is the one-row
    # bar; the report's script sets the bar's measured height (it is two rows with the
    # financial views, wraps on narrow windows, and is not sticky on phones).
    "--sticky-bar-height": "64px",
    # Typography. System stacks, never a webfont: a report fetches nothing when opened.
    "--font-sans": ("ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"),
    "--font-serif": "ui-serif, Georgia, 'Times New Roman', serif",
    "--font-mono": "ui-monospace, 'SF Mono', Menlo, Consolas, monospace",
    # Depth
    "--shadow-soft": "0 1px 2px 0 rgba(28,28,58,0.04), 0 1px 3px 0 rgba(28,28,58,0.06)",
}

#: The tokens the examples landing page uses. A subset rather than the whole palette
#: because that page is a plain index of links, with no diff states to colour. Naming
#: the subset here rather than re-typing the values is the point: the landing page and
#: the reports it links cannot drift apart, because there is only one set of values.
LANDING_SUBSET: tuple[str, ...] = (
    "--background",
    "--foreground",
    "--card",
    "--primary",
    "--muted-foreground",
    "--border",
    "--radius",
    "--font-sans",
    "--font-serif",
    "--shadow-soft",
)


def declarations(names: tuple[str, ...] | None = None, indent: str = "  ") -> str:
    """The `--name: value;` lines for `names`, defaulting to the whole palette.

    Emitted one per line so a change to any single value is a one-line diff in the
    generated artifacts, which are committed and reviewed.
    """
    selected = PALETTE if names is None else {n: PALETTE[n] for n in names}
    return "\n".join(f"{indent}{name}: {value};" for name, value in selected.items())


def root_block(names: tuple[str, ...] | None = None) -> str:
    """A complete `:root { ... }` rule, newline-terminated."""
    return f":root {{\n{declarations(names)}\n}}\n"
