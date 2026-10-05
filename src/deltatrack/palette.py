"""The palette for DeltaTrack's report, its examples landing page, and the upload pages.

The values live in `styles/tokens.css`; this module reads them so each surface can emit
them. The report (`formatters/diff_html.py`) and the published examples landing page
(`scripts/render_examples.py`) embed them when rendered. The upload pages get a
generated block in `web/webapp/css/styles.css` (`scripts/render_webapp_css.py`),
committed because those pages are served as static files. The processing tab in
`web/webapp/js/compare.js` copies the upload page's tokens when it opens (#773).

Each surface *embeds* its tokens rather than linking one shared file. A report has to
render with no network at all (ADR 0011), so it cannot fetch a stylesheet. "One palette"
therefore means one source generated into each surface, never one file they all link.

Each surface embeds only the tokens its own rules use (`referenced`), not the whole file,
so a token one surface needs ships nowhere else. An unreferenced token would ship in
every report and style nothing, which is how eleven of them accumulated with the whole
suite green (#667). `tests/test_committed_examples.py` gates both halves: what a rendered
report declares, and that every token here is used by some surface.

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
_VAR_REFERENCE = re.compile(r"var\(\s*(--[\w-]+)")


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


def referenced(css: str, palette: dict[str, str] = PALETTE) -> tuple[str, ...]:
    """The tokens `css` uses, in palette order.

    A `var()` fallback counts as a use, and a token whose value refers to another brings
    that one along, so nothing a rule needs is left out. Comments are not uses: a token
    named only in prose would otherwise ship and style nothing. A name the palette does
    not declare raises, because a browser resolves it to nothing without complaint.
    """
    pending = set(_VAR_REFERENCE.findall(_COMMENT.sub("", css)))
    found: set[str] = set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        if name not in palette:
            raise ValueError(f"{name} is used but styles/tokens.css does not declare it")
        found.add(name)
        pending.update(_VAR_REFERENCE.findall(palette[name]))
    return tuple(name for name in palette if name in found)


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
