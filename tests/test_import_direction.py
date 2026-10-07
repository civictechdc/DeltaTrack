"""The report viewer and the diff engine load apart (#801).

The viewer reads the canonical diff document and draws the report (ADR 0007); the engine
parses bills, matches them and builds that document. They meet only at the document. If
loading one loads the other, a change on either side can reach the other through an
import nobody intended, and the report cannot be drawn without the PDF library, which a
browser build of the engine cannot load.

**Two measurements, because each misses what the other sees** (#814):

- *What a module loads*, measured in a fresh interpreter by importing it and reading
  ``sys.modules``. That is the real closure, through every intermediate module, rather
  than one file's import statements. It cannot see an import inside a function, which
  runs only when that function does, and a branch the test never reaches never runs.
- *Every import statement the source holds*, at any depth, read from the syntax tree:
  inside functions, in error branches, under ``TYPE_CHECKING``, and the dynamic
  ``importlib.import_module``/``__import__`` calls. It cannot see what an allowed module
  loads in turn, which the first measurement does.

A saved document is also rendered with the PDF library blocked, and what the render
loaded is checked against the same allowlist.

The engine side has three known exceptions: the command-line entry points inside
``diff_bill`` and ``diff_pdf`` import ``compare/`` from inside a function, a cycle #62
tracks. They are named below by file, function and module, so a fourth is a decision
and the list must shrink when #62 moves them.

**The viewer's roster is an allowlist.** Every ``deltatrack`` module the viewer loads must
be named below. A new dependency is then a decision someone reads in review, not a silent
widening; a denylist would guard a shrinking subset as the engine grows.

**The engine's roster is derived** from what is on disk: every module that is not the
viewer, a helper it may load, or ``compare/`` (which assembles both). It has a floor, so a
rename cannot empty a check into a pass.
"""

from __future__ import annotations

import ast
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

#: The modules on the viewer's allowlist that the engine may load too. Named rather than
#: derived, so a module newly added to the viewer is viewer-only until someone says not.
SHARED = frozenset(
    {
        "deltatrack",
        "deltatrack.formatters",
        "deltatrack.formatters._text",
        "deltatrack.formatters.schema_version",
        "deltatrack.palette",
    }
)

#: Viewer-only modules, which no part of the engine may load.
VIEWER_ONLY = VIEWER_MAY_LOAD - SHARED


def _all_modules() -> list[str]:
    """Every module in the package, from the files on disk."""
    names = []
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = path.relative_to(PACKAGE.parent).with_suffix("").parts
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def _is_compare(module: str) -> bool:
    """``compare/`` or a module in it; a sibling merely named ``compare_*`` is not."""
    return module == "deltatrack.compare" or module.startswith("deltatrack.compare.")


def _engine_modules() -> list[str]:
    """Everything that is not the viewer, a helper it may load, or ``compare/``, which
    assembles the engine and the renderer and so may load both."""
    return [m for m in _all_modules() if m not in VIEWER_MAY_LOAD and not _is_compare(m)]


DIFFERS = ("deltatrack.diff_bill", "deltatrack.diff_pdf")

#: The engine's imports of ``compare/``, by (module, enclosing function, imported module):
#: the command-line entry points that call back into the comparison they belong beside
#: (#62). Each is function-local, so the loaded-closure tests do not see it.
ENGINE_CLI_IMPORTS = frozenset(
    {
        ("deltatrack.diff_bill", "cmd_compare", "deltatrack.compare.xml"),
        ("deltatrack.diff_pdf", "render_pdf_diff_html", "deltatrack.compare.pdf"),
        ("deltatrack.diff_pdf", "render_pdf_diff_json", "deltatrack.compare.pdf"),
    }
)

#: Stands in for a dynamic import whose target is not a literal, so no allowlist can pass it.
NON_LITERAL = "<non-literal dynamic import>"


def _source_path(module: str) -> Path:
    path = PACKAGE.parent.joinpath(*module.split("."))
    return path / "__init__.py" if path.is_dir() else path.with_suffix(".py")


def _imports(module: str, source: str) -> list[tuple[int, str, str]]:
    """Every import in ``source`` as ``(line, enclosing function, module imported)``.

    Walks the whole tree, so an import inside a function, a branch or ``TYPE_CHECKING``
    counts. ``from X import Y`` is read as ``X.Y`` when that is a module, since
    ``from deltatrack import matching`` loads ``deltatrack.matching``, not ``deltatrack``.
    The loaders ``importlib.import_module``, ``builtins.__import__`` and ``importlib.resources.files``
    (which imports the package it is given) count too, under any name they are bound to, with
    an absolute literal target; any other target is :data:`NON_LITERAL`.

    Not read: ``exec``/``eval``, ``pkgutil``, ``runpy`` and the ``importlib.util`` spec
    machinery. Nobody writes a dependency that way here, and the closure tests still see
    any of them that runs at import time or on a rendered path.
    """
    modules = set(_all_modules())
    tree = ast.parse(source)
    loaders = {"import_module", "__import__", "files"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("importlib", "importlib.resources", "builtins"):
            loaders |= {a.asname for a in node.names if a.name in ("import_module", "files", "__import__") and a.asname}
    package = module if _source_path(module).name == "__init__.py" else module.rpartition(".")[0]
    found: list[tuple[int, str, str]] = []

    def record(line: int, where: str, name: str) -> None:
        found.append((line, where, name))

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else where
            if isinstance(child, ast.Import):
                for alias in child.names:
                    record(child.lineno, where, alias.name)
            elif isinstance(child, ast.ImportFrom):
                base = child.module or ""
                if child.level:
                    anchor = package.split(".")[: len(package.split(".")) - (child.level - 1)]
                    base = ".".join([*anchor, *([base] if base else [])])
                for alias in child.names:
                    name = f"{base}.{alias.name}"
                    record(child.lineno, where, name if name in modules else base)
            elif isinstance(child, ast.Call):
                func = child.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if called in loaders:
                    target = child.args[0] if child.args else None
                    literal = isinstance(target, ast.Constant) and isinstance(target.value, str)
                    # A relative target resolves against a runtime package argument.
                    absolute = literal and not target.value.startswith(".")
                    record(child.lineno, where, target.value if absolute else NON_LITERAL)
            visit(child, inner)

    visit(tree, "<module>")
    return found


def _viewer_violations(module: str, source: str) -> list[str]:
    """The imports in a viewer-side file that the viewer may not load."""

    def refused(name: str) -> bool:
        top = name.split(".")[0]
        return (
            name == NON_LITERAL
            or (top == "deltatrack" and name not in VIEWER_MAY_LOAD)
            or top in VIEWER_MUST_NOT_LOAD_EXTERNAL
        )

    return [
        f"{module}:{line} ({where}) imports {name}" for line, where, name in _imports(module, source) if refused(name)
    ]


def _engine_violations(module: str, source: str) -> list[str]:
    """The imports in an engine file of the viewer or of ``compare/``, outside
    :data:`ENGINE_CLI_IMPORTS`."""
    return [
        f"{module}:{line} ({where}) imports {name}"
        for line, where, name in _imports(module, source)
        if (name == NON_LITERAL or name in VIEWER_ONLY or _is_compare(name))
        and (module, where, name) not in ENGINE_CLI_IMPORTS
    ]


def _loaded_by(module: str, *, block: tuple[str, ...] = ()) -> set[str]:
    """Every module a fresh interpreter holds after importing ``module``."""
    script = (
        "import json, sys\n"
        f"for name in {list(block)!r}:\n"
        "    sys.modules[name] = None\n"
        f"import {module}\n"
        "print(json.dumps(sorted(k for k, v in sys.modules.items() if v is not None)))\n"
    )
    # -P keeps the checkout root off sys.path, so its script shims and tests/ cannot load.
    result = subprocess.run([sys.executable, "-P", "-c", script], capture_output=True, text=True, cwd=ROOT)
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


def test_the_rosters_name_real_modules():
    """A renamed module must not empty a check into a pass."""
    engine = _engine_modules()
    assert len(engine) >= 15, engine
    assert {"deltatrack.formatters.canonical", "deltatrack.parsers.pdf_text", *DIFFERS} <= set(engine)
    assert VIEWER_ONLY <= _loaded_by("deltatrack.formatters.diff_html"), "a viewer-only module is not loaded"


@pytest.mark.parametrize("module", _engine_modules())
def test_the_engine_does_not_load_the_viewer(module):
    viewer = sorted(VIEWER_ONLY & _loaded_by(module))
    assert not viewer, f"{module} loads the viewer: {viewer}"


@pytest.mark.parametrize("module", DIFFERS)
def test_the_differs_do_not_load_the_comparison_entry_points(module):
    """``compare/`` assembles parse → diff → document; a differ loading it runs backwards."""
    compare = sorted(m for m in _loaded_by(module) if _is_compare(m))
    assert not compare, f"{module} loads {compare}"


@pytest.mark.parametrize("module", sorted(VIEWER_MAY_LOAD))
def test_no_viewer_file_imports_outside_the_allowlist(module):
    """Every import the viewer's files hold, wherever it sits, stays on the allowlist."""
    violations = _viewer_violations(module, _source_path(module).read_text())
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("module", [*_engine_modules(), *sorted(SHARED)])
def test_no_engine_file_imports_the_viewer_or_the_comparison(module):
    """No engine file imports a viewer-only module or ``compare/``, at any depth, except
    the command-line entry points named in :data:`ENGINE_CLI_IMPORTS`. The shared modules
    are held to the same rule, since the engine loads them."""
    violations = _engine_violations(module, _source_path(module).read_text())
    assert not violations, "\n".join(violations)


def test_the_cli_exceptions_still_exist():
    """An exception that no longer matches an import is stale: it would pass a new import
    that happened to take its place. When #62 moves the entry points, this list shrinks."""
    present = {
        (module, where, name)
        for module in {m for m, _, _ in ENGINE_CLI_IMPORTS}
        for _, where, name in _imports(module, _source_path(module).read_text())
    }
    assert ENGINE_CLI_IMPORTS <= present, sorted(ENGINE_CLI_IMPORTS - present)


@pytest.mark.parametrize(
    ("source", "caught"),
    [
        ("from deltatrack import matching", "deltatrack.matching"),
        ("from deltatrack.formatters import canonical", "deltatrack.formatters.canonical"),
        ("from deltatrack.formatters.canonical import _pdf_tree_payload", "deltatrack.formatters.canonical"),
        ("import deltatrack.amounts", "deltatrack.amounts"),
        ("from .. import similarity", "deltatrack.similarity"),
        ("from .canonical import xml_diff_to_canonical", "deltatrack.formatters.canonical"),
        ("def f():\n    if x:\n        from deltatrack import matching", "deltatrack.matching"),
        ("try:\n    pass\nexcept ValueError:\n    import deltatrack.version_stems", "deltatrack.version_stems"),
        (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from deltatrack.diff_pdf import PdfDiff",
            "deltatrack.diff_pdf",
        ),
        ("import importlib\nimportlib.import_module('deltatrack.amounts')", "deltatrack.amounts"),
        ("import importlib\nimportlib.import_module(name)", NON_LITERAL),
        ("__import__('deltatrack' + '.matching')", NON_LITERAL),
        ("from importlib import import_module as load\nload('deltatrack.diff_pdf')", "deltatrack.diff_pdf"),
        ("from builtins import __import__ as load\nload('deltatrack.matching')", "deltatrack.matching"),
        ("import builtins\nbuiltins.__import__('deltatrack.matching')", "deltatrack.matching"),
        ("import importlib\nimportlib.import_module('.canonical', __package__)", NON_LITERAL),
        ("from importlib.resources import files\nfiles('deltatrack.compare.pdf')", "deltatrack.compare.pdf"),
        ("from importlib.resources import files as f\nf('deltatrack.amounts')", "deltatrack.amounts"),
        ("import pypdfium2", "pypdfium2"),
    ],
)
def test_the_scan_reads_every_import_form(source, caught):
    """The scan is checked against each form of import it reads, so a form it stops
    reading fails here rather than passing every file."""
    violations = _viewer_violations("deltatrack.formatters.canonical_view", source)
    assert any(v.endswith(f"imports {caught}") for v in violations), violations


@pytest.mark.parametrize(
    ("where", "imported", "flagged"),
    [
        ("cmd_compare", "deltatrack.compare.xml", False),
        ("cmd_diff", "deltatrack.compare.xml", True),
        ("cmd_compare", "deltatrack.compare.pdf", True),
        ("cmd_compare", "deltatrack.formatters.diff_html", True),
        ("cmd_compare", "deltatrack.compare", True),
    ],
)
def test_the_engine_scan_exempts_only_the_named_cli_import(where, imported, flagged):
    """An exception covers one import in one function: the same import elsewhere in the
    module, or another import in that function, is still reported."""
    package, _, name = imported.rpartition(".")
    source = f"def {where}():\n    from {package} import {name}\n"
    assert bool(_engine_violations("deltatrack.diff_bill", source)) == flagged


def test_the_rosters_exclude_only_compare_itself():
    """A module merely named like ``compare`` is still engine, and still scanned."""
    assert _is_compare("deltatrack.compare") and _is_compare("deltatrack.compare.pdf")
    assert not _is_compare("deltatrack.compare_cli")


def test_the_scan_passes_allowed_imports():
    """The allowed forms the viewer uses today are not reported."""
    source = (
        "from deltatrack.formatters.schema_version import SCHEMA_VERSION\n"
        "from deltatrack.formatters._text import word_diff\n"
        "from deltatrack.palette import referenced\n"
        "from importlib.resources import files\n"
        "files('deltatrack')\n"
        "import json\n"
    )
    assert _viewer_violations("deltatrack.formatters.diff_html", source) == []


def _saved_document(pipeline: str) -> dict:
    from deltatrack.compare.pdf import compare_pdfs
    from deltatrack.compare.xml import compare_xml
    from tests.corpus_paths import fixture_path

    build, suffix = (compare_xml, "xml") if pipeline == "xml" else (compare_pdfs, "pdf")
    old = fixture_path("118-hr-8752", f"1_reported-in-house.{suffix}")
    new = fixture_path("118-hr-8752", f"2_engrossed-in-house.{suffix}")
    return build(old.read_bytes(), new.read_bytes())


@pytest.mark.parametrize("pipeline", ["xml", "pdf"])
def test_a_saved_document_renders_without_the_pdf_library(tmp_path, pipeline):
    """The report is drawn from the document alone, so the PDF library is never needed,
    and the render loads nothing off the viewer's allowlist. The PDF document carries
    ``print_breaks``, so the print layout runs too, a path an XML document never takes."""
    from deltatrack.formatters.diff_html import format_diff_html

    document = _saved_document(pipeline)
    if pipeline == "pdf":
        assert document.get("print_breaks") and document.get("full_text"), "the print path would not run"
    saved = tmp_path / "diff.json"
    saved.write_text(json.dumps(document))
    loaded = tmp_path / "loaded.json"
    script = (
        "import json, sys\n"
        "sys.modules['pypdfium2'] = None\n"
        "from deltatrack.formatters.diff_html import format_diff_html\n"
        f"html = format_diff_html(json.loads(open({str(saved)!r}).read()))\n"
        f"open({str(loaded)!r}, 'w').write(json.dumps(sorted(k for k, v in sys.modules.items() if v is not None)))\n"
        "sys.stdout.write(html)\n"
    )
    result = subprocess.run([sys.executable, "-P", "-c", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert result.stdout == format_diff_html(document)
    engine = _engine_loads(set(json.loads(loaded.read_text())))
    assert not engine, f"rendering a {pipeline} document loaded {engine}"
