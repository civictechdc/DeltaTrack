"""The upload pages' generated blocks come from `styles/` (#773, #774).

`web/webapp/css/styles.css` is committed, because the upload pages are served as static
files, so nothing regenerates it on the way to a browser. Two blocks in it are generated:
the tokens from `tokens.css`, and the shared button and badge rules from
`components.css`. If either source changes and the script is not rerun, or someone edits
a block by hand, the upload pages quietly stop matching the report they open. These
checks compare the committed file with what the generator would write, and never write
it themselves.
"""

from __future__ import annotations

import difflib
from itertools import islice

import pytest

from scripts.render_webapp_css import (
    COMPONENTS,
    COMPONENTS_BEGIN,
    COMPONENTS_END,
    PROCESSING_TAB,
    STYLESHEET,
    TOKENS_BEGIN,
    TOKENS_END,
    render,
)

REGENERATE = "Run `uv run python scripts/render_webapp_css.py` and commit the result."

TOKENS = f"{TOKENS_BEGIN}{TOKENS_END}"
COMPONENTS_BLOCK = f"{COMPONENTS_BEGIN}{COMPONENTS_END}"


def test_the_committed_generated_blocks_are_what_the_generator_writes():
    committed = STYLESHEET.read_text()
    fresh = render(committed, COMPONENTS.read_text(), PROCESSING_TAB.read_text())

    diff = difflib.unified_diff(committed.splitlines(True), fresh.splitlines(True), "committed", "generated", n=0)
    assert committed == fresh, f"web/webapp/css/styles.css's generated blocks are stale. {REGENERATE}\n" + "".join(
        islice(diff, 12)
    )


@pytest.mark.parametrize(
    "stylesheet",
    [
        pytest.param("a {}\n", id="no-markers"),
        pytest.param(f"{TOKENS}{TOKENS}{COMPONENTS_BLOCK}", id="two-token-blocks"),
        pytest.param(f"{TOKENS}{COMPONENTS_BLOCK}{COMPONENTS_BLOCK}", id="two-component-blocks"),
        pytest.param(f"{TOKENS_END}{TOKENS_BEGIN}{COMPONENTS_BLOCK}", id="tokens-reversed"),
        pytest.param(f"{TOKENS}{COMPONENTS_END}{COMPONENTS_BEGIN}", id="components-reversed"),
        pytest.param(f"{COMPONENTS_BLOCK}{TOKENS}", id="components-before-tokens"),
    ],
)
def test_malformed_markers_are_refused_rather_than_partly_rewritten(stylesheet):
    # A second, stale block would still be in the file and win the cascade.
    with pytest.raises(ValueError, match="GENERATED"):
        render(stylesheet, "", "")
