"""The renderer decides legibility; the differ decides correspondence. Separately.

ADR 0020 names one coupling in the rendering layer and requires its removal: the HTML
renderer recomputed the differ's word-overlap score and compared it against the differ's
own cutoff, so changing what "the same provision" means also changed what a reader saw,
in the same edit, with no way to test the two apart. `formatters/_text.word_diff` now
reads `LEGIBILITY_THRESHOLD`, which the rendering layer owns.

**The two numbers are equal today and nothing here asserts that.** A test pinning
`LEGIBILITY_THRESHOLD == SIMILARITY_THRESHOLD` would rebuild the coupling inside the
suite and would redden the moment the differ is legitimately retuned (#368, #170 are both
open and both would move it). The legibility cutoff is pinned to a *literal* instead.

## Why three gates and not one

Each of the first three is defeated by a mistake the others catch, which is the whole
reason the set has three members rather than one:

`test_the_legibility_cutoff_is_pinned_to_its_own_literal`
    catches a retuned render. Defeated on its own by a **dead constant**: declare
    `LEGIBILITY_THRESHOLD = 0.4`, then write `threshold: float = 0.4` in the signature.
    The constant is then decorative and this test still passes.

`test_no_formatter_depends_on_the_correspondence_cutoff`
    catches re-coupling. Defeated on its own by the same dead constant, and by a bare
    literal: an import-graph gate cannot tell a wired constant from an unwired one.

`test_word_diff_takes_its_default_from_the_legibility_cutoff`
    closes exactly that hole by reading the *default expression* rather than its value.
    Defeated on its own by pointing `LEGIBILITY_THRESHOLD` at the wrong number, which the
    literal pin catches.

The fourth gate is behavioural and is the only one that shows the cutoff has any effect at
all: two synthetic cases either side of it, driven through the real `_prose_body_html`
caller rather than through `word_diff`. Calling `word_diff` with an explicit threshold
exercises the parameter, and the parameter is not what this slice changed.

## Why the controls are source mutations, not monkeypatches

`word_diff`'s threshold is a default argument, bound once at import. Rebinding
`_text.LEGIBILITY_THRESHOLD` (or `similarity.SIMILARITY_THRESHOLD`) at runtime therefore
cannot reach it, and a runtime-perturbation test asserting "the render did not move"
passes whether or not the separation exists -- it passed on the coupled code this slice
replaced. That is a fact about the language, not about the design, so production keeps its
ordinary bound default and the negative controls edit the source and re-run instead. The
controls are recorded in the pull request rather than shipped, because each one is a
deliberate defect.

## What is deliberately NOT asserted

That the committed examples are insensitive to this cutoff. They are today: every change
in the corpus that reaches `_prose_body_html` scores at or above 0.4, because the differ's
own cutoff upstream guarantees it, so the rendered examples stay byte-identical even with
the legibility cutoff destroyed. That blind spot was measured and is recorded in the pull
request as a reason the example gate is regression evidence rather than architecture
proof. It is not pinned here: a future matching or corpus change may legitimately make the
examples exercise the cutoff, and that would be an improvement, not a regression.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from deltatrack.formatters import _text
from deltatrack.formatters.diff_html import _build_card
from deltatrack.formatters.view_model import ChangeView

#: The symbol this boundary is about. Named once so the failure messages and the scan
#: cannot drift apart.
CORRESPONDENCE_CUTOFF = "SIMILARITY_THRESHOLD"

FORMATTERS_DIR = Path(_text.__file__).resolve().parent


def _formatter_sources() -> list[Path]:
    """Every module in the rendering layer. Derived, so a new formatter is covered by
    existing, rather than by someone remembering to add it to a list."""
    return sorted(FORMATTERS_DIR.rglob("*.py"))


# --- Gate 1: the value ---------------------------------------------------------------


def test_the_legibility_cutoff_is_pinned_to_its_own_literal():
    """Pinned to a number, never to the differ's constant. See the module docstring."""
    assert _text.LEGIBILITY_THRESHOLD == 0.4


# --- Gate 2: no dependency on the correspondence cutoff ------------------------------


def _correspondence_cutoff_references(path: Path) -> list[str]:
    """Every way a module could reach the differ's cutoff, found on the AST.

    AST rather than text: `_text.py` discusses `SIMILARITY_THRESHOLD` at length in its own
    comments and docstring, and prose about a boundary is not a breach of it. Covers the
    three reachable spellings -- a direct or aliased `from ... import`, an attribute access
    on the module, and a bare name -- rather than only the import line, so moving the
    reference into a function body or behind `import deltatrack.similarity` does not slip
    past.

    Scoped to the one symbol. A blanket ban on importing `deltatrack.similarity` would be a
    module-dependency rule ADR 0020 does not ask for: `text_similarity` is a measure, not a
    policy, and a formatter that one day needs to compute a ratio is not thereby making a
    correspondence decision.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom | ast.Import):
            found += [
                f"line {node.lineno}: imports {alias.name}"
                for alias in node.names
                if alias.name == CORRESPONDENCE_CUTOFF
            ]
        elif isinstance(node, ast.Attribute) and node.attr == CORRESPONDENCE_CUTOFF:
            found.append(f"line {node.lineno}: attribute access .{CORRESPONDENCE_CUTOFF}")
        elif isinstance(node, ast.Name) and node.id == CORRESPONDENCE_CUTOFF:
            found.append(f"line {node.lineno}: name {CORRESPONDENCE_CUTOFF}")
    return found


def test_no_formatter_depends_on_the_correspondence_cutoff():
    """The boundary itself. A violation means the renderer is reading the differ's policy
    again, whichever way it is spelled.

    Proven capable of failing: restoring
    ``from deltatrack.similarity import SIMILARITY_THRESHOLD`` to ``_text.py`` reddens it,
    and so does routing the constant in through ``diff_html`` instead.
    """
    violations = {path.name: refs for path in _formatter_sources() if (refs := _correspondence_cutoff_references(path))}
    assert not violations, (
        f"the rendering layer reads the differ's {CORRESPONDENCE_CUTOFF}: {violations}. "
        "Whether two observations are the same provision (assignment, ADR 0020) and whether an "
        "inline word-diff is legible (rendering) are different questions. Use "
        "formatters._text.LEGIBILITY_THRESHOLD."
    )


# --- Gate 3: the default is wired to the constant, not to a copy of its value ---------


def _word_diff_threshold_default() -> ast.expr:
    """The expression `word_diff`'s `threshold` default is written as, not its value."""
    module = ast.parse(Path(_text.__file__).read_text(encoding="utf-8"))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "word_diff")
    positional = function.args.posonlyargs + function.args.args
    # Defaults align to the END of the positional list, so index from the right.
    offset = len(positional) - len(function.args.defaults)
    index = next(i for i, arg in enumerate(positional) if arg.arg == "threshold")
    return function.args.defaults[index - offset]


def test_word_diff_takes_its_default_from_the_legibility_cutoff():
    """Read the default's EXPRESSION, because reading its value cannot tell the two apart.

    ``threshold: float = 0.4`` and ``threshold: float = LEGIBILITY_THRESHOLD`` behave
    identically today, and the first leaves the constant decorative: retuning it would then
    change nothing a reader sees, silently, with gates 1 and 2 both green. That is the
    false-green this gate exists for.

    Proven capable of failing: replacing the default with a bare ``0.4`` reddens it, and so
    does pointing it back at the differ's cutoff.
    """
    default = _word_diff_threshold_default()
    assert isinstance(default, ast.Name) and default.id == "LEGIBILITY_THRESHOLD", (
        "word_diff's threshold default must be the name LEGIBILITY_THRESHOLD, not "
        f"{ast.dump(default)}. A literal copy of the value leaves the constant decorative: "
        "the renderer's cutoff could then be retuned with no effect and nothing failing."
    )


# --- Gate 4: the cutoff actually decides, through the real caller ---------------------
#
# Ratios are asserted alongside the render rather than assumed. A fixture that drifted to
# the wrong side of 0.4 would otherwise keep passing while testing the opposite case, which
# is the failure mode a hand-built boundary fixture actually has.
#
# Synthetic on purpose. The committed corpus contains no change below the cutoff at all --
# the differ splits those into a removal plus an addition long before rendering -- so the
# stacked branch is unreachable from real bill text and a corpus-derived fixture could only
# ever test one side.

#: 11 words vs 10, sharing 4. Ratio 0.381: immediately below the cutoff.
BELOW_OLD = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo"
BELOW_NEW = "alpha bravo charlie delta lima mike november oscar papa quebec"

#: 10 words vs 10, sharing 4. Ratio exactly 0.4, which pins the boundary as inclusive:
#: `word_diff` stacks on `ratio < threshold`, so equality must render inline.
AT_OLD = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
AT_NEW = "alpha bravo charlie delta lima mike november oscar papa quebec"

#: Comfortably above, so a suite that stacked everything would not read as passing.
ABOVE_OLD = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
ABOVE_NEW = "alpha bravo charlie delta echo foxtrot november oscar papa quebec"


def _prose_card(old_text: str, new_text: str) -> str:
    """A `modified` card through the production path: `_build_card` -> `_prose_body_html`.

    Not `word_diff` directly. Passing a threshold explicitly exercises the parameter, and
    the parameter is not what changed -- the production default is.
    """
    return _build_card(
        ChangeView(
            change_type="modified",
            heading_html="TITLE I &gt; Customs",
            nav_label_html="TITLE I &gt; Customs",
            section_number="",
            citation_html="",
            degraded=False,
            move_info_html="",
            old_text=old_text,
            new_text=new_text,
        ),
        0,
    )


@pytest.mark.parametrize(
    ("old_text", "new_text", "ratio"),
    [
        pytest.param(AT_OLD, AT_NEW, 0.4, id="exactly-at-the-cutoff"),
        pytest.param(ABOVE_OLD, ABOVE_NEW, 0.6, id="above-the-cutoff"),
    ],
)
def test_a_pair_at_or_above_the_cutoff_renders_inline(old_text: str, new_text: str, ratio: float):
    assert _text.word_diff(old_text, new_text, threshold=0.0) is not None  # sanity: a diff exists
    assert _ratio(old_text, new_text) == ratio, "fixture drifted off its intended side of the cutoff"
    html = _prose_card(old_text, new_text)
    assert '<div class="change__body diff-inline">' in html
    assert '<div class="old-text">' not in html


def test_a_pair_immediately_below_the_cutoff_renders_stacked():
    """0.381 against a 0.4 cutoff: the render must stack.

    Both texts are non-empty, so this exercises the THRESHOLD branch and not
    `_prose_body_html`'s separate empty-side guard, which stacks regardless of any cutoff.
    """
    assert BELOW_OLD and BELOW_NEW
    assert _ratio(BELOW_OLD, BELOW_NEW) == pytest.approx(0.38095238, abs=1e-8)
    assert _ratio(BELOW_OLD, BELOW_NEW) < _text.LEGIBILITY_THRESHOLD
    html = _prose_card(BELOW_OLD, BELOW_NEW)
    assert '<div class="old-text">' in html
    assert '<div class="new-text">' in html
    assert '<div class="change__body diff-inline">' not in html


def _ratio(old_text: str, new_text: str) -> float:
    """The ratio `word_diff` compares against its threshold, computed independently.

    Transcribed rather than imported: asking `word_diff` for its own score could not detect
    that the score changed, and what these fixtures need to pin is where they sit relative
    to the cutoff.
    """
    import difflib

    return difflib.SequenceMatcher(None, old_text.split(), new_text.split()).ratio()
