"""Regenerate the upload pages' stylesheet blocks and the processing tab's base styles.

Run from anywhere after changing `tokens.css`, `base.css` or `components.css` in
`src/deltatrack/styles/`, or after a rule in `web/webapp/css/styles.css` or the
processing tab in `web/webapp/js/compare.js` starts or stops using a token:

    uv run python scripts/render_webapp_css.py

The upload pages are served as plain static files (FastAPI's `StaticFiles` mount, and
`web/webapp/.htaccess` for Apache), so their stylesheet has to be a committed file
rather than one built per request. Only the blocks between the GENERATED markers are
rewritten; the rules around them are edited by hand. `tests/test_webapp_css.py` fails if
the committed file is not what this script would write (#773, #774, #804).

The first block is the `:root` tokens. It holds the tokens the stylesheet's rules use,
the shared ones included, plus those the processing tab uses. The tab copies its
colours from the upload page that opens it, so a token only the tab uses still has to be
declared here, or the tab would read an empty value.

The other two are verbatim copies, in the order a report embeds them: `base.css`, the
reset, body and heading rules, then `components.css`, the button and badge rules. Both
sit before the hand-written rules, which may set what is particular to these pages but
should not restyle a shared control.

The processing tab is written with `document.write`, so it can't link a stylesheet
either. `compare.js` gets its own copy of `base.css`, as the `BASE_CSS` template literal
between its GENERATED markers, and the tab's rules follow it.

Kept out of `render_examples.py` on purpose: tests drive that script's `main()` with
only its output folder redirected, so a write to a checkout path there would run on
every test run and could repair a stale block before the drift test looked at it.
"""

from __future__ import annotations

from pathlib import Path

from deltatrack.palette import referenced, root_block

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEBAPP = PROJECT_ROOT / "web" / "webapp"
STYLESHEET = WEBAPP / "css" / "styles.css"
PROCESSING_TAB = WEBAPP / "js" / "compare.js"
BASE = PROJECT_ROOT / "src" / "deltatrack" / "styles" / "base.css"
COMPONENTS = PROJECT_ROOT / "src" / "deltatrack" / "styles" / "components.css"

TOKENS_BEGIN = "/* BEGIN GENERATED tokens by scripts/render_webapp_css.py. Do not edit by hand. */\n"
TOKENS_END = "/* END GENERATED tokens */\n"
BASE_BEGIN = (
    "/* BEGIN GENERATED base by scripts/render_webapp_css.py, "
    "copied from src/deltatrack/styles/base.css. Do not edit by hand. */\n"
)
BASE_END = "/* END GENERATED base */\n"
COMPONENTS_BEGIN = (
    "/* BEGIN GENERATED components by scripts/render_webapp_css.py, "
    "copied from src/deltatrack/styles/components.css. Do not edit by hand. */\n"
)
COMPONENTS_END = "/* END GENERATED components */\n"
TAB_BASE_BEGIN = (
    "  // BEGIN GENERATED base by scripts/render_webapp_css.py, "
    "copied from src/deltatrack/styles/base.css. Do not edit by hand.\n"
)
TAB_BASE_END = "  // END GENERATED base\n"


def _block(text: str, begin: str, end: str, name: str = STYLESHEET.name) -> tuple[int, int]:
    """Where the text between `begin` and `end` starts and stops."""
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError(f"{name} must contain exactly one {begin.strip()!r} and one {end.strip()!r}")
    start = text.index(begin) + len(begin)
    stop = text.index(end)
    if stop < start:
        raise ValueError(f"{name}'s {end.strip()!r} comes before its {begin.strip()!r}")
    return start, stop


def render_processing_tab(processing_tab: str, base: str) -> str:
    """`processing_tab` with its `BASE_CSS` block rewritten from `base.css`."""
    if any(text in base for text in ("`", "${", "\\")):
        # Each would end or alter the JavaScript template literal the copy sits in.
        raise ValueError("base.css can't contain a backtick, '${' or a backslash: compare.js embeds it in a template")
    start, stop = _block(processing_tab, TAB_BASE_BEGIN, TAB_BASE_END, PROCESSING_TAB.name)
    base = base if base.endswith("\n") else base + "\n"
    return processing_tab[:start] + f"  const BASE_CSS = `\n{base}`;\n" + processing_tab[stop:]


def render(stylesheet: str, base: str, components: str, processing_tab: str) -> str:
    """`stylesheet` with all three generated blocks rewritten from the current sources.

    Raises on missing, repeated or out-of-order markers rather than rewriting part of
    the file: a second block left stale would win the cascade over the fresh one.
    """
    tokens_start, tokens_stop = _block(stylesheet, TOKENS_BEGIN, TOKENS_END)
    base_start, base_stop = _block(stylesheet, BASE_BEGIN, BASE_END)
    components_start, components_stop = _block(stylesheet, COMPONENTS_BEGIN, COMPONENTS_END)
    if not tokens_stop < base_start <= base_stop < components_start:
        raise ValueError(f"{STYLESHEET.name}'s GENERATED blocks must come in the order tokens, base, components")
    base, components = (text if text.endswith("\n") else text + "\n" for text in (base, components))

    head = stylesheet[:tokens_start]
    after_tokens = stylesheet[tokens_stop:base_start]
    after_base = stylesheet[base_stop:components_start]
    tail = stylesheet[components_stop:]
    tokens = root_block(referenced(head + after_tokens + base + after_base + components + tail + processing_tab))
    return head + tokens + after_tokens + base + after_base + components + tail


def main() -> None:
    base = BASE.read_text()
    # The tab first: the stylesheet declares the tokens the regenerated tab uses.
    processing_tab = render_processing_tab(PROCESSING_TAB.read_text(), base)
    PROCESSING_TAB.write_text(processing_tab)
    STYLESHEET.write_text(render(STYLESHEET.read_text(), base, COMPONENTS.read_text(), processing_tab))
    print(f"Wrote {PROCESSING_TAB} and {STYLESHEET}")


if __name__ == "__main__":
    main()
