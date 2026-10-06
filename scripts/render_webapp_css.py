"""Regenerate the two generated blocks in the upload pages' stylesheet.

Run from anywhere after changing `src/deltatrack/styles/tokens.css` or
`src/deltatrack/styles/components.css`, or after a rule in `web/webapp/css/styles.css`
or the processing tab in `web/webapp/js/compare.js` starts or stops using a token:

    uv run python scripts/render_webapp_css.py

The upload pages are served as plain static files (FastAPI's `StaticFiles` mount, and
`web/webapp/.htaccess` for Apache), so their stylesheet has to be a committed file
rather than one built per request. Only the blocks between the GENERATED markers are
rewritten; the rules around them are edited by hand. `tests/test_webapp_css.py` fails if
the committed file is not what this script would write (#773, #774).

The first block is the `:root` tokens. It holds the tokens the stylesheet's rules use,
the shared components included, plus those the processing tab uses. The tab copies its
colours from the upload page that opens it, so a token only the tab uses still has to be
declared here, or the tab would read an empty value.

The second block is a verbatim copy of `components.css`, the button and badge rules the
report embeds too, so the two surfaces style each control with the same rule. It sits
before the hand-written rules, which may place a control but should not restyle it.

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
COMPONENTS = PROJECT_ROOT / "src" / "deltatrack" / "styles" / "components.css"

TOKENS_BEGIN = "/* BEGIN GENERATED tokens by scripts/render_webapp_css.py. Do not edit by hand. */\n"
TOKENS_END = "/* END GENERATED tokens */\n"
COMPONENTS_BEGIN = (
    "/* BEGIN GENERATED components by scripts/render_webapp_css.py, "
    "copied from src/deltatrack/styles/components.css. Do not edit by hand. */\n"
)
COMPONENTS_END = "/* END GENERATED components */\n"


def _block(stylesheet: str, begin: str, end: str) -> tuple[int, int]:
    """Where the text between `begin` and `end` starts and stops."""
    if stylesheet.count(begin) != 1 or stylesheet.count(end) != 1:
        raise ValueError(f"{STYLESHEET.name} must contain exactly one {begin.strip()!r} and one {end.strip()!r}")
    start = stylesheet.index(begin) + len(begin)
    stop = stylesheet.index(end)
    if stop < start:
        raise ValueError(f"{STYLESHEET.name}'s {end.strip()!r} comes before its {begin.strip()!r}")
    return start, stop


def render(stylesheet: str, components: str, processing_tab: str) -> str:
    """`stylesheet` with both generated blocks rewritten from the current sources.

    Raises on missing, repeated or out-of-order markers rather than rewriting part of
    the file: a second block left stale would win the cascade over the fresh one.
    """
    tokens_start, tokens_stop = _block(stylesheet, TOKENS_BEGIN, TOKENS_END)
    components_start, components_stop = _block(stylesheet, COMPONENTS_BEGIN, COMPONENTS_END)
    if components_start < tokens_stop:
        raise ValueError(f"{STYLESHEET.name}'s GENERATED components block must come after its tokens block")
    if not components.endswith("\n"):
        components += "\n"

    head = stylesheet[:tokens_start]
    between = stylesheet[tokens_stop:components_start]
    tail = stylesheet[components_stop:]
    tokens = root_block(referenced(head + between + components + tail + processing_tab))
    return head + tokens + between + components + tail


def main() -> None:
    STYLESHEET.write_text(render(STYLESHEET.read_text(), COMPONENTS.read_text(), PROCESSING_TAB.read_text()))
    print(f"Wrote {STYLESHEET}")


if __name__ == "__main__":
    main()
