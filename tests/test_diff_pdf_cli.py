"""Tests for the diff_pdf CLI entry point (issue #25)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from deltatrack.diff_pdf import build_parser, main
from tests.corpus_paths import fixture_path

V1 = fixture_path("118-hr-8752", "1_reported-in-house.pdf")
V2 = fixture_path("118-hr-8752", "2_engrossed-in-house.pdf")
REPORT = "<!doctype html><p>old → new</p><p>⚠ unanchored</p>"

_UTF8_ROUTE_CHILD = r"""
import locale
import os
import sys
from pathlib import Path

REPORT = "<!doctype html><p>old \u2192 new</p><p>\u26a0 unanchored</p>"


def _ascii(value):
    return str(value).encode("ascii", "backslashreplace").decode("ascii")


def _status(name):
    print("DT627_STATUS=" + name)
    return 0


def _utf8_name(value):
    return str(value).lower().replace("-", "").replace("_", "") in {"utf8", "utf"}


def main():
    route, output, old_input, new_input = sys.argv[1:]
    try:
        locale_encoding = locale.getencoding()
        with open(os.devnull, "w") as probe:
            file_encoding = probe.encoding
    except Exception as exc:
        print("DT627_STATUS=locale-unavailable")
        print("DT627_DETAIL=" + type(exc).__name__)
        return 11

    print("DT627_DEFAULT_ENCODING=" + _ascii(locale_encoding))
    print("DT627_FILE_ENCODING=" + _ascii(file_encoding))
    if _utf8_name(locale_encoding) or _utf8_name(file_encoding):
        return _status("locale-unavailable")

    try:
        if route != "pdf":
            print("DT627_STATUS=setup-error")
            print("DT627_DETAIL=unknown-route")
            return 12
        import deltatrack.diff_pdf as diff_pdf

        diff_pdf.render_pdf_diff_html = lambda *_args, **_kwargs: REPORT
        route_main = lambda: diff_pdf.main(
            [old_input, new_input, "--output", output]
        )
    except Exception as exc:
        print("DT627_STATUS=setup-error")
        print("DT627_DETAIL=" + type(exc).__name__)
        return 12

    try:
        route_main()
    except UnicodeEncodeError:
        return _status("unicode-error")
    except SystemExit as exc:
        print("DT627_STATUS=route-error")
        print("DT627_DETAIL=SystemExit:" + _ascii(exc.code))
        return 13
    except Exception as exc:
        print("DT627_STATUS=route-error")
        print("DT627_DETAIL=" + type(exc).__name__)
        return 13

    try:
        report = Path(output).read_bytes()
    except Exception as exc:
        print("DT627_STATUS=byte-mismatch")
        print("DT627_DETAIL=" + type(exc).__name__)
        return 0
    if report != REPORT.encode("utf-8"):
        return _status("byte-mismatch")
    try:
        decoded = report.decode("utf-8")
    except UnicodeDecodeError:
        return _status("decode-error")
    if "\u2192" not in decoded or "\u26a0" not in decoded:
        return _status("marker-mismatch")
    return _status("ok")


raise SystemExit(main())
"""


def _run_non_utf8_report_child(route: str, output: Path, old_input: Path, new_input: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.update({"PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C", "LANG": "C"})
    pythonpath = [str(repo_root / "src"), str(repo_root)]
    if environment.get("PYTHONPATH"):
        pythonpath.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(pythonpath)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _UTF8_ROUTE_CHILD,
            route,
            str(output),
            str(old_input),
            str(new_input),
        ],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
    )
    status = next(
        (line for line in result.stdout.splitlines() if line.startswith("DT627_STATUS=")),
        "<missing>",
    )
    assert result.returncode == 0, (
        f"child setup/route failure ({status}); stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    assert status == "DT627_STATUS=ok", (
        f"report route did not preserve UTF-8 ({status}); stdout={result.stdout!r}; stderr={result.stderr!r}"
    )

    report = output.read_bytes()
    assert report == REPORT.encode("utf-8")
    decoded = report.decode("utf-8")
    assert "→" in decoded
    assert "⚠" in decoded


def test_fixtures_committed():
    """Fail-closed floor (#326): both PDFs are committed and manifested, so an absent
    one is a broken checkout, not an optional case. This used to be a class-level
    ``skipif`` written when the corpus was fetched rather than committed; a skip is
    green, so deleting either file would have silently turned the two CLI tests below
    off (the fail-open shape epic #288 collects, same as #287)."""
    absent = sorted(str(p) for p in (V1, V2) if not p.exists())
    assert not absent, f"committed diff_pdf CLI fixtures absent from checkout: {absent}"


class TestParser:
    def test_positional_and_output(self):
        args = build_parser().parse_args(["a.pdf", "b.pdf", "-o", "out.html"])
        assert args.v1_pdf == Path("a.pdf")
        assert args.v2_pdf == Path("b.pdf")
        assert args.output == Path("out.html")

    def test_label_defaults(self):
        args = build_parser().parse_args(["a.pdf", "b.pdf"])
        assert args.v1_label is None
        assert args.v2_label is None


class TestCli:
    def test_writes_html_file(self, tmp_path):
        out = tmp_path / "diff.html"
        main([str(V1), str(V2), "-o", str(out)])
        html = out.read_text()
        assert html.lstrip().lower().startswith("<!doctype html") or "<html" in html.lower()
        assert "reported-in-house" in html
        assert "engrossed-in-house" in html
        # Delegating to compare_pdfs_html means the report carries the
        # full-bill view + embedded export, not just the changed-section cards.
        assert "full-bill" in html
        assert "diff.json" in html

    def test_html_output_uses_utf8_when_host_default_is_cp1252(self, tmp_path):
        """The real CLI writer is tested under a verified non-UTF-8 child locale."""
        output = tmp_path / "diff.html"
        v1_pdf = tmp_path / "old.pdf"
        v2_pdf = tmp_path / "new.pdf"
        v1_pdf.write_bytes(b"old")
        v2_pdf.write_bytes(b"new")
        _run_non_utf8_report_child("pdf", output, v1_pdf, v2_pdf)

    def test_stdout_when_no_output(self, capsys):
        main([str(V1), str(V2)])
        captured = capsys.readouterr()
        assert "<html" in captured.out.lower()
