"""Colours come from `styles/tokens.css`, and the pairs we list stay readable (#775).

The tokens file is the single style source (#752): change a value there, regenerate, and
every surface follows. A colour typed straight into a rule is invisible to that, so it
keeps its old value through the next palette change while the suite stays green.

Three checks:

- No surface ships a literal colour outside a token declaration. Reads what each
  surface ships, the way the token census in `test_committed_examples.py` does.
- Each pair listed in `styles/contrast.toml` meets its declared threshold. The list is
  kept by hand: it says nothing about pairs it doesn't name.
- The report's find box outline is at least 3:1 against what is behind it, read from
  the report's stylesheet.
"""

from __future__ import annotations

import re
import tomllib
from importlib.resources import files

import pytest

from deltatrack.formatters import diff_html
from deltatrack.palette import PALETTE
from scripts import render_examples
from scripts.render_webapp_css import PROCESSING_TAB, STYLESHEET, WEBAPP

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
_STYLE_ATTRIBUTE = re.compile(r"""\sstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""")
#: The processing tab's CSS: its generated `BASE_CSS` copy and its own `PENDING_CSS` rules.
_TAB_CSS = re.compile(r"const (?:BASE|PENDING)_CSS = [^`]*`(.*?)`", re.S)

#: The innermost `{ ... }`, which is a rule's declarations even inside `@media`.
_DECLARATION_BLOCK = re.compile(r"\{([^{}]*)\}")
_STRING = re.compile(r""""[^"]*"|'[^']*'""")
_URL = re.compile(r"url\([^)]*\)", re.I)

_HEX = re.compile(r"#[0-9a-f]{3,8}\b", re.I)
_COLOUR_FUNCTION = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix)\(", re.I)
_IDENTIFIER = re.compile(r"[a-z][a-z0-9-]*", re.I)
_CUSTOM_PROPERTY_NAME = re.compile(r"--[\w-]+")

#: CSS Color Level 4's named colours. `transparent` and `currentcolor` are left out:
#: neither is a palette choice.
_NAMED_COLOURS = frozenset(
    """
    aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
    blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk
    crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki
    darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
    darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue
    dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro
    ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo
    ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral
    lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon
    lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime
    limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple
    mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred
    midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab
    orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip
    peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue
    saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue
    slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet
    wheat white whitesmoke yellow yellowgreen
    """.split()
)


def _surfaces() -> dict[str, str]:
    """The CSS each surface ships, by surface.

    The report's stylesheet is read as the formatter builds it, so a literal in a
    package `.css` fails here directly rather than only after the examples are
    regenerated. The upload pages' stylesheet is the committed file, because that is
    what the server sends; `test_webapp_css.py` keeps its generated blocks current.
    """
    index = render_examples.INDEX_TEMPLATE.format(
        tokens=render_examples.INDEX_TOKENS, base=render_examples.INDEX_BASE, cards=""
    )
    markup = []
    for page in sorted(WEBAPP.glob("*.html")):
        html = page.read_text()
        markup += _STYLE_BLOCK.findall(html)
        markup += [f"{{{double or single}}}" for double, single in _STYLE_ATTRIBUTE.findall(html)]
    return {
        "report": diff_html._CSS,
        "examples index": "\n".join(_STYLE_BLOCK.findall(index)),
        "upload pages": STYLESHEET.read_text(),
        "upload page markup": "\n".join(markup),
        "processing tab": "\n".join(_TAB_CSS.findall(PROCESSING_TAB.read_text())),
    }


def _rules(css: str) -> list[list[tuple[str, str]]]:
    """Each rule's `property: value` pairs, comments, strings and `url()`s blanked.

    A list rather than a dict, so a property declared twice in one rule keeps both.
    """
    rules = []
    for block in _DECLARATION_BLOCK.findall(_COMMENT.sub("", css)):
        block = _URL.sub("url()", _STRING.sub('""', block))
        rule = []
        for declaration in block.split(";"):
            if ":" in declaration:
                name, value = declaration.split(":", 1)
                rule.append((name.strip(), " ".join(value.split())))
        rules.append(rule)
    return rules


def _declarations(css: str) -> list[tuple[str, str]]:
    return [declaration for rule in _rules(css) for declaration in rule]


def _literal_colours(css: str) -> list[str]:
    """The declarations in `css` that paint a colour not taken from the tokens file.

    A token's own declaration is exempt only with the value `tokens.css` gives it, which
    is how each surface's generated `:root` reads. Any other custom property holding a
    colour is a second source, and so is a token redeclared with a different value.
    """
    flagged = []
    for name, value in _declarations(css):
        if name in PALETTE and value == " ".join(PALETTE[name].split()):
            continue
        bare = _CUSTOM_PROPERTY_NAME.sub("", value)
        named = {word.lower() for word in _IDENTIFIER.findall(bare)} & _NAMED_COLOURS
        if _HEX.search(bare) or _COLOUR_FUNCTION.search(bare) or named:
            flagged.append(f"{name}: {value}")
    return flagged


def test_no_surface_paints_a_colour_outside_the_tokens_file():
    assert list(WEBAPP.glob("*.html")), f"no upload pages found in {WEBAPP}; their markup would go unscanned"
    surfaces = _surfaces()
    for surface, css in surfaces.items():
        if surface != "upload page markup":
            assert _declarations(css), f"no declarations parsed from the {surface}; this check would vacuously pass"

    literals = {surface: found for surface, css in surfaces.items() if (found := _literal_colours(css))}
    assert not literals, (
        f"literal colours outside styles/tokens.css: {literals}. Add a token for each to "
        "tokens.css and refer to it with var(--name), then regenerate "
        "(`uv run python scripts/render_webapp_css.py` and `uv run python scripts/render_examples.py`)."
    )


@pytest.mark.parametrize(
    "css",
    [
        pytest.param("a { color: #abc; }", id="hex-3"),
        pytest.param("a { color: #aabbcc80; }", id="hex-8"),
        pytest.param("a { box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1); }", id="rgba"),
        pytest.param("a { color: rgb(0 0 0); }", id="rgb"),
        pytest.param("a { color: hsl(0 0% 0%); }", id="hsl"),
        pytest.param("a { color: oklch(50% 0.1 200); }", id="oklch"),
        pytest.param("a { color: color-mix(in srgb, var(--primary) 50%, var(--card)); }", id="color-mix"),
        pytest.param("a { border: 1px solid Red; }", id="named"),
        pytest.param("a { color: var(--primary, #fff); }", id="var-fallback"),
        pytest.param("a { --rogue: #123456; }", id="untracked-custom-property"),
        pytest.param(":root { --primary: #000000; }", id="token-redeclared-with-another-value"),
    ],
)
def test_each_way_of_writing_a_colour_is_caught(css):
    assert _literal_colours(css), f"{css!r} paints a literal colour but was not flagged"


#: WCAG 2.2 AA minimums, by the `kind` a pair declares in `contrast.toml`.
_MINIMUM_RATIO = {"text": 4.5, "non-text": 3.0}
_SOLID_HEX = re.compile(r"#(?:[0-9a-f]{3}|[0-9a-f]{6})", re.I)
_VAR_REFERENCE = re.compile(r"var\(\s*(--[\w-]+)")


def _pairs() -> list[dict[str, str]]:
    text = files("deltatrack").joinpath("styles", "contrast.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)["pair"]


def _relative_luminance(hex_colour: str) -> float:
    digits = hex_colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(digit * 2 for digit in digits)
    linear = []
    for i in (0, 2, 4):
        channel = int(digits[i : i + 2], 16) / 255
        linear.append(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
    red, green, blue = linear
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_relative_luminance(foreground), _relative_luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_every_declared_pair_meets_wcag_aa():
    pairs = _pairs()
    assert pairs, "no pairs parsed from styles/contrast.toml; this check would vacuously pass"

    problems = []
    seen = set()
    for pair in pairs:
        foreground, background, kind = pair["foreground"], pair["background"], pair["kind"]
        if (foreground, background) in seen:
            problems.append(f"{foreground} on {background} is listed twice")
        seen.add((foreground, background))
        if kind not in _MINIMUM_RATIO:
            problems.append(f"{foreground} on {background} has kind {kind!r}; use one of {sorted(_MINIMUM_RATIO)}")
            continue
        values = [PALETTE.get(foreground, ""), PALETTE.get(background, "")]
        if not all(_SOLID_HEX.fullmatch(value) for value in values):
            problems.append(f"{foreground} on {background}: both must be solid hex tokens in tokens.css, got {values}")
            continue
        ratio = _contrast(*values)
        if ratio < _MINIMUM_RATIO[kind]:
            problems.append(
                f"{foreground} on {background} is {ratio:.2f}:1, below {_MINIMUM_RATIO[kind]}:1 ({pair['where']})"
            )

    assert not problems, "colour pairs in styles/contrast.toml fail WCAG AA:\n" + "\n".join(problems)


def _tokens_in_rule(css: str, selector: str, prop: str) -> list[str]:
    """The tokens `prop` names in each rule written for exactly `selector`."""
    rule = re.compile(r"(?:^|[},\s])" + re.escape(selector) + r"\s*\{([^{}]*)\}")
    found = []
    for body in rule.findall(_COMMENT.sub("", css)):
        for declaration in body.split(";"):
            name, _, value = declaration.partition(":")
            if name.strip() == prop:
                found += _VAR_REFERENCE.findall(value)
    return found


def test_the_find_box_outline_stands_out_from_its_fill_and_surroundings():
    """The report's find box is identifiable by its outline: 3:1 or more (WCAG 1.4.11).

    Read from the report's stylesheet, so its border returned to `--border` fails here
    (1.39:1 on its fill, 1.29:1 on the action bar). It sits in the action bar on wide
    screens and in the bottom find bar on narrow ones.
    """
    css = diff_html._CSS
    borders = _tokens_in_rule(css, ".find-bar input", "border")
    fill = _tokens_in_rule(css, ".find-bar input", "background")
    surroundings = _tokens_in_rule(css, ".action-bar", "background") + _tokens_in_rule(css, ".find-bar", "background")
    assert borders and fill and surroundings, (
        f"find box colours not found (border {borders}, fill {fill}, surroundings {surroundings}); "
        "this check would vacuously pass"
    )

    weak = {
        f"{border} on {background}": f"{ratio:.2f}:1"
        for border in borders
        for background in fill + surroundings
        if (ratio := _contrast(PALETTE[border], PALETTE[background])) < 3.0
    }
    assert not weak, f"the find box's outline is below 3:1 against what is behind it: {weak}"
