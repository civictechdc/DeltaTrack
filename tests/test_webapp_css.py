"""The upload pages' and processing tab's generated blocks come from `styles/` (#773, #774, #804).

`web/webapp/css/styles.css` is committed, because the upload pages are served as static
files, so nothing regenerates it on the way to a browser. Three blocks in it are
generated: the tokens from `tokens.css`, the reset, body and heading rules from
`base.css`, and the shared button and badge rules from `components.css`. If a source
changes and the script is not rerun, or someone edits a block by hand, the upload pages
quietly stop matching the report they open. `web/webapp/js/compare.js` carries one more,
its copy of `base.css` for the processing tab. These checks compare the committed files
with what the generator would write, and never write them themselves.
"""

from __future__ import annotations

import difflib
from itertools import islice

import pytest

from scripts.render_webapp_css import (
    BASE,
    BASE_BEGIN,
    BASE_END,
    COMPONENTS,
    COMPONENTS_BEGIN,
    COMPONENTS_END,
    PROCESSING_TAB,
    STYLESHEET,
    TOKENS_BEGIN,
    TOKENS_END,
    render,
    render_processing_tab,
)

REGENERATE = "Run `uv run python scripts/render_webapp_css.py` and commit the result."

TOKENS = f"{TOKENS_BEGIN}{TOKENS_END}"
BASE_BLOCK = f"{BASE_BEGIN}{BASE_END}"
COMPONENTS_BLOCK = f"{COMPONENTS_BEGIN}{COMPONENTS_END}"


def test_the_committed_generated_blocks_are_what_the_generator_writes():
    committed = STYLESHEET.read_text()
    fresh = render(committed, BASE.read_text(), COMPONENTS.read_text(), PROCESSING_TAB.read_text())

    diff = difflib.unified_diff(committed.splitlines(True), fresh.splitlines(True), "committed", "generated", n=0)
    assert committed == fresh, f"web/webapp/css/styles.css's generated blocks are stale. {REGENERATE}\n" + "".join(
        islice(diff, 12)
    )


def test_the_processing_tabs_base_block_is_what_the_generator_writes():
    # The tab is written with document.write and can't link base.css, so it carries a copy.
    committed = PROCESSING_TAB.read_text()
    fresh = render_processing_tab(committed, BASE.read_text())

    diff = difflib.unified_diff(committed.splitlines(True), fresh.splitlines(True), "committed", "generated", n=0)
    assert committed == fresh, f"web/webapp/js/compare.js's generated BASE_CSS is stale. {REGENERATE}\n" + "".join(
        islice(diff, 12)
    )


# Each case is malformed in one way only, so it is refused for that reason and not for a
# block it happens to leave out.
@pytest.mark.parametrize(
    "stylesheet",
    [
        pytest.param("a {}\n", id="no-markers"),
        pytest.param(f"{TOKENS}{COMPONENTS_BLOCK}", id="no-base-block"),
        pytest.param(f"{TOKENS}{TOKENS}{BASE_BLOCK}{COMPONENTS_BLOCK}", id="two-token-blocks"),
        pytest.param(f"{TOKENS}{BASE_BLOCK}{BASE_BLOCK}{COMPONENTS_BLOCK}", id="two-base-blocks"),
        pytest.param(f"{TOKENS}{BASE_BLOCK}{COMPONENTS_BLOCK}{COMPONENTS_BLOCK}", id="two-component-blocks"),
        pytest.param(f"{TOKENS_END}{TOKENS_BEGIN}{BASE_BLOCK}{COMPONENTS_BLOCK}", id="tokens-reversed"),
        pytest.param(f"{TOKENS}{BASE_END}{BASE_BEGIN}{COMPONENTS_BLOCK}", id="base-reversed"),
        pytest.param(f"{TOKENS}{BASE_BLOCK}{COMPONENTS_END}{COMPONENTS_BEGIN}", id="components-reversed"),
        pytest.param(f"{BASE_BLOCK}{TOKENS}{COMPONENTS_BLOCK}", id="base-before-tokens"),
        pytest.param(f"{TOKENS}{COMPONENTS_BLOCK}{BASE_BLOCK}", id="base-after-components"),
        pytest.param(f"{BASE_BLOCK}{COMPONENTS_BLOCK}{TOKENS}", id="tokens-last"),
    ],
)
def test_malformed_markers_are_refused_rather_than_partly_rewritten(stylesheet):
    # A second, stale block would still be in the file and win the cascade.
    with pytest.raises(ValueError, match="GENERATED"):
        render(stylesheet, "", "", "")


def test_a_well_formed_stylesheet_is_accepted():
    # The refusals above are only meaningful if the same blocks, in order, are not refused.
    assert render(f"{TOKENS}{BASE_BLOCK}{COMPONENTS_BLOCK}", "", "", "")
