"""The report viewer and the diff engine load apart (#801).

The viewer reads the canonical diff document and draws the report (ADR 0007); the engine
parses bills, matches them and builds that document. They meet only at the document. If
loading one loads the other, a change on either side can reach the other through an
import nobody intended, and the report cannot be drawn without the PDF library, which a
browser build of the engine cannot load.

**What is checked is what a module loads**, measured in a fresh interpreter by importing
it and reading ``sys.modules``. That is the real closure, through every intermediate
module, rather than one file's import statements. Function-local imports are not loaded
by importing a module and are not counted here: the command-line entry points inside
``diff_bill`` and ``diff_pdf`` call back into ``compare/`` that way, which is a separate
question from which code loads with which.

**The viewer's roster is an allowlist.** Every ``deltatrack`` module the viewer loads must
be named below. A new dependency is then a decision someone reads in review, not a silent
widening; a denylist would guard a shrinking subset as the engine grows.

**The engine's rosters are derived** from what is on disk (every parser module, both
differs), and each has a floor, so a rename cannot empty a check into a pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "deltatrack"

#: The modules that draw the report from a document.
VIEWER = (
    "deltatrack.formatters.diff_html",
    "deltatrack.formatters.canonical_view",
)

#: Every ``deltatrack`` module the viewer may load: itself, the view model it fills, the
#: print layout and word diff it draws with, the palette, the contract version it checks,
#: and the packages that hold them.
VIEWER_MAY_LOAD = frozenset(
    {
        "deltatrack",
        "deltatrack.formatters",
        "deltatrack.formatters.diff_html",
        "deltatrack.formatters.canonical_view",
        "deltatrack.formatters.view_model",
        "deltatrack.formatters.print_layout",
        "deltatrack.formatters._text",
        "deltatrack.formatters.schema_version",
        "deltatrack.palette",
    }
)

#: Third-party modules the viewer must never load: the PDF library has native code.
VIEWER_MUST_NOT_LOAD_EXTERNAL = ("pypdfium2",)

#: Viewer-only modules, which no part of the engine may load.
VIEWER_ONLY = frozenset(
    {
        "deltatrack.formatters.diff_html",
        "deltatrack.formatters.canonical_view",
        "deltatrack.formatters.view_model",
        "deltatrack.formatters.print_layout",
    }
)


def _parser_modules() -> list[str]:
    return sorted(f"deltatrack.parsers.{p.stem}" for p in (PACKAGE / "parsers").glob("*.py") if p.stem != "__init__")


DIFFERS = ("deltatrack.diff_bill", "deltatrack.diff_pdf")


def _loaded_by(module: str, *, block: tuple[str, ...] = ()) -> set[str]:
    """Every module a fresh interpreter holds after importing ``module``."""
    script = (
        "import json, sys\n"
        f"for name in {list(block)!r}:\n"
        "    sys.modules[name] = None\n"
        f"import {module}\n"
        "print(json.dumps(sorted(k for k, v in sys.modules.items() if v is not None)))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, f"importing {module} failed:\n{result.stderr}"
    return set(json.loads(result.stdout))


def _engine_loads(loaded: set[str]) -> list[str]:
    return sorted(m for m in loaded if m.startswith("deltatrack") and m not in VIEWER_MAY_LOAD)


@pytest.mark.parametrize("module", VIEWER)
def test_the_viewer_loads_only_its_allowlist(module):
    loaded = _loaded_by(module)
    assert not _engine_loads(loaded), f"{module} loads engine modules: {_engine_loads(loaded)}"
    external = [m for m in VIEWER_MUST_NOT_LOAD_EXTERNAL if m in loaded]
    assert not external, f"{module} loads {external}"


def test_the_engine_rosters_are_not_empty():
    assert len(_parser_modules()) >= 4, _parser_modules()
    assert all((PACKAGE / f"{m.rsplit('.', 1)[1]}.py").exists() for m in DIFFERS)


@pytest.mark.parametrize("module", [*_parser_modules(), *DIFFERS])
def test_the_engine_does_not_load_the_viewer(module):
    viewer = sorted(VIEWER_ONLY & _loaded_by(module))
    assert not viewer, f"{module} loads the viewer: {viewer}"


@pytest.mark.parametrize("module", DIFFERS)
def test_the_differs_do_not_load_the_comparison_entry_points(module):
    """``compare/`` assembles parse → diff → document; a differ loading it runs backwards."""
    compare = sorted(m for m in _loaded_by(module) if m.startswith("deltatrack.compare"))
    assert not compare, f"{module} loads {compare}"


def test_a_saved_document_renders_without_the_pdf_library(tmp_path):
    """The report is drawn from the document alone, so the PDF library is never needed."""
    from deltatrack.compare.xml import compare_xml
    from deltatrack.formatters.diff_html import format_diff_html
    from tests.corpus_paths import fixture_path

    old = fixture_path("118-hr-8752", "1_reported-in-house.xml")
    new = fixture_path("118-hr-8752", "2_engrossed-in-house.xml")
    document = compare_xml(old.read_bytes(), new.read_bytes())
    saved = tmp_path / "diff.json"
    saved.write_text(json.dumps(document))
    script = (
        "import json, sys\n"
        "sys.modules['pypdfium2'] = None\n"
        "from deltatrack.formatters.diff_html import format_diff_html\n"
        f"sys.stdout.write(format_diff_html(json.loads(open({str(saved)!r}).read())))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert result.stdout == format_diff_html(document)
