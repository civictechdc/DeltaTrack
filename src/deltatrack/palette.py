"""The palette for DeltaTrack's report and its examples landing page.

The values live in `styles/tokens.css`; this module reads them so the renderers can emit
them. Two surfaces take their values from here: the report (`formatters/diff_html.py`), and
the published examples landing page (`scripts/render_examples.py`), which embeds a subset.
Nothing else does yet. The web app declares its own values in `webapp/css/styles.css`,
and the loading tab in `webapp/js/compare.js` hardcodes four of them, so editing the
tokens reaches neither and no test would report the divergence (#773).

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

import re
from importlib.resources import files

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_DECLARATION = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


def _read_tokens() -> dict[str, str]:
    css = _COMMENT.sub("", files("deltatrack").joinpath("styles", "tokens.css").read_text(encoding="utf-8"))
    pairs = _DECLARATION.findall(css)
    tokens = dict(pairs)
    if len(tokens) != len(pairs):
        # A repeated name would silently keep its last value, so one of two edits is lost.
        raise ValueError("styles/tokens.css declares a token more than once")
    return tokens


# In file order, which is what a reader sees in the emitted `:root`.
PALETTE: dict[str, str] = _read_tokens()

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
