"""`palette.referenced`: which tokens a surface embeds (#773).

Each surface ships only the tokens its rules use, so a token left out here is a `var()`
that resolves to nothing in a browser, with nothing failing. The real palette holds
only literal values today, so the cases below use a small one that exercises the
shapes the real file can grow into.
"""

from __future__ import annotations

import pytest

from deltatrack.palette import referenced

_PALETTE = {
    "--a": "#000",
    "--b": "#111",
    "--alias": "var(--b)",
    "--c": "#222",
}


def test_selects_what_the_rules_use_in_palette_order():
    assert referenced(".x { color: var(--c); background: var(--a); }", _PALETTE) == ("--a", "--c")


def test_a_fallback_and_an_aliased_token_are_both_kept():
    # Dropping either would leave a declared rule pointing at nothing.
    assert referenced(".x { color: var(--alias); } .y { color: var(--c, var(--a)); }", _PALETTE) == (
        "--a",
        "--b",
        "--alias",
        "--c",
    )


def test_a_token_named_only_in_a_comment_is_not_used():
    assert referenced("/* var(--a) */ .x { color: var(--c); }", _PALETTE) == ("--c",)


def test_an_undeclared_token_raises():
    with pytest.raises(ValueError, match="--missing"):
        referenced(".x { color: var(--missing); }", _PALETTE)
