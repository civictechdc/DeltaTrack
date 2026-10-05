"""The upload pages' token block is generated from `styles/tokens.css` (#773).

`web/webapp/css/styles.css` is committed, because the upload pages are served as static
files, so nothing regenerates it on the way to a browser. If a token changes in
`tokens.css` and the script is not rerun, or someone edits the block by hand, the upload
pages quietly stop matching the report they open. These checks compare the committed
file with what the generator would write, and never write it themselves.
"""

from __future__ import annotations

import pytest

from scripts.render_webapp_css import BEGIN, END, PROCESSING_TAB, STYLESHEET, render

REGENERATE = "Run `uv run python scripts/render_webapp_css.py` and commit the result."


def test_the_committed_token_block_is_what_the_generator_writes():
    committed = STYLESHEET.read_text()
    fresh = render(committed, PROCESSING_TAB.read_text())

    stale = [
        f"  committed: {old}\n  generated: {new}"
        for old, new in zip(committed.splitlines(), fresh.splitlines(), strict=False)
        if old != new
    ]
    assert committed == fresh, f"web/webapp/css/styles.css's token block is stale. {REGENERATE}\n" + "\n".join(
        stale[:5]
    )


@pytest.mark.parametrize(
    "stylesheet",
    [
        pytest.param("a {}\n", id="no-markers"),
        pytest.param(f"{BEGIN}{END}{BEGIN}{END}", id="two-blocks"),
        pytest.param(f"{END}{BEGIN}", id="reversed"),
    ],
)
def test_malformed_markers_are_refused_rather_than_partly_rewritten(stylesheet):
    # A second, stale block would still be in the file and win the cascade.
    with pytest.raises(ValueError, match="GENERATED"):
        render(stylesheet, "")
