"""One bill pair, both surfaces, one document (#693).

Nothing ran a single input through the command line *and* the HTTP endpoint and
compared what came back. ``tests/test_pipeline_parity.py`` compares the XML and PDF
*pipelines*; ``tests/test_surface_boundary.py`` enforces import direction. Neither
looks across the two surfaces, which is why ``./diff_bill.py compare --format json``
could return the engine's internal diff dictionary while
``POST /api/compare?output=json`` returned the canonical contract, two documents
sharing two of eight top-level keys, with the whole suite green.

The gate is document equality rather than "both look canonical", because a shape check
passes on two documents that are wrong in the same way. The schema test covers the
direction equality cannot: both surfaces drifting together, away from
``schema/canonical-diff.schema.json``.

**Equality is of the parsed documents, not of the bytes**, and the two surfaces really
do serialize differently: the command writes ``json.dumps(..., indent=2)``, which
indents and escapes non-ASCII, while the endpoint returns Starlette's ``JSONResponse``,
which emits compact UTF-8. Measured on the fixture pair below, 551,433 bytes against
380,599, with ``\u2014`` on one side and raw em dash bytes on the other. Byte identity
would mean indenting the HTTP response to match a file on disk, which costs every API
caller about 45% more payload and buys nothing: ``schema/canonical-diff.md`` specifies
a document, and #691 (the epic making every surface produce the same answer) asks the
surfaces to agree on the answer, not on the whitespace. So do not "strengthen" this
into a byte comparison; it would fail on formatting while saying nothing about whether
the two agree.

Both formats are held to it. ``./diff_pdf.py`` gained ``--format json`` in the same
change, and the PDF half is the one with no prior behaviour to preserve, so pinning it
now is what keeps it from acquiring a second vocabulary the way the XML half did.

Real bill documents, so ``@pytest.mark.slow`` (see AGENTS.md). The fixture pair is
committed and manifested in both formats, so these fail closed rather than skipping.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from deltatrack.diff_bill import build_parser, cmd_compare
from deltatrack.diff_pdf import main as diff_pdf_main
from tests.corpus_paths import FIXTURES_DIR

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema" / "canonical-diff.schema.json"

BILL_DIR = FIXTURES_DIR / "118-hr-8752"
V1_STEM = "1_reported-in-house"
V2_STEM = "2_engrossed-in-house"


def _cli_json(tmp_dir: Path, old: Path, new: Path) -> str:
    """The command's JSON output, driven through its real argument parser.

    Dispatches on the extension, because the two commands are meant to be the same
    offer: `./diff_bill.py compare --format json` and `./diff_pdf.py --format json`.
    """
    out = tmp_dir / "cli.json"
    if old.suffix == ".xml":
        cmd_compare(build_parser().parse_args(["compare", str(old), str(new), "--format", "json", "-o", str(out)]))
    else:
        diff_pdf_main([str(old), str(new), "--format", "json", "-o", str(out)])
    return out.read_text(encoding="utf-8")


def _endpoint_json(old: Path, new: Path) -> dict:
    """``POST /api/compare?output=json``, driven through the real FastAPI route."""
    from fastapi.testclient import TestClient

    from web.app import app

    fmt = old.suffix.lstrip(".")
    with open(old, "rb") as start, open(new, "rb") as end:
        response = TestClient(app).post(
            f"/api/compare?format={fmt}&output=json",
            files={
                "start_file": (old.name, start, "application/octet-stream"),
                "end_file": (new.name, end, "application/octet-stream"),
            },
        )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module", params=["xml", "pdf"])
def unprefixed_pair(request, tmp_path_factory) -> tuple[Path, Path]:
    """The fixture pair copied under stems carrying no ``<n>_`` ordinal.

    The un-numbered case: a draft or a renamed upload, whose label must survive intact
    and whose ordinal is unknown on every surface. The numbered case is the corpus pair.
    """
    ext = request.param
    tmp = tmp_path_factory.mktemp(f"unprefixed-{ext}")
    old, new = tmp / f"reported-in-house.{ext}", tmp / f"engrossed-in-house.{ext}"
    shutil.copyfile(BILL_DIR / f"{V1_STEM}.{ext}", old)
    shutil.copyfile(BILL_DIR / f"{V2_STEM}.{ext}", new)
    return old, new


@pytest.fixture(scope="module", params=["xml", "pdf"])
def corpus_pair(request) -> tuple[Path, Path]:
    """The committed fixture pair under its real ``<n>_<label>`` names."""
    ext = request.param
    return BILL_DIR / f"{V1_STEM}.{ext}", BILL_DIR / f"{V2_STEM}.{ext}"


@pytest.mark.slow
def test_the_command_and_the_endpoint_return_the_same_document(tmp_path, unprefixed_pair):
    """Same two files in, the same canonical document out.

    This is the gate #693 is verified by, and reverting the routing in
    ``diff_bill.cmd_compare`` is the mutation that turns it red: the internal diff
    dictionary shares two top-level keys with the canonical document and none of its
    change fields.

    See the module docstring for why this compares parsed documents rather than bytes.
    """
    old, new = unprefixed_pair

    assert json.loads(_cli_json(tmp_path, old, new)) == _endpoint_json(old, new)


def _example_versions(monkeypatch, tmp_path: Path, fmt: str) -> dict:
    """``versions`` from the published-example renderer, run for this pair into a temp dir."""
    from scripts import render_examples

    spec = next(
        s
        for s in render_examples.EXAMPLES_TO_RENDER
        if s.bill_dir == BILL_DIR.name
        and fmt in s.formats
        and (s.v1_filename_stem, s.v2_filename_stem) == (V1_STEM, V2_STEM)
    )
    monkeypatch.setattr(render_examples, "EXAMPLES", tmp_path)
    html = render_examples.RENDERERS[fmt](spec).read_text(encoding="utf-8")
    embedded = html.split('<script type="application/json" id="diff-data">', 1)[1].split("</script>", 1)[0]
    return json.loads(embedded.replace("<\\/", "</"))["versions"]


@pytest.mark.slow
def test_numbered_corpus_names_give_one_version_identity_on_every_surface(tmp_path, monkeypatch, corpus_pair):
    """The real ``<n>_<label>`` names: command, endpoint and published example agree.

    #692: the same pair headed itself three ways (the upload kept the ``1_`` prefix and
    every PDF surface but the examples dropped the ordinal), because six call sites each
    read the filename their own way. Whole-document equality between command and
    endpoint, ``versions`` included, is what the shared resolver buys; the expected
    identity is spelled out so the surfaces cannot agree on a wrong answer.
    """
    old, new = corpus_pair
    fmt = old.suffix.lstrip(".")
    cli = json.loads(_cli_json(tmp_path, old, new))
    endpoint = _endpoint_json(old, new)

    expected = {
        "v1": {"label": "reported-in-house", "version_number": 1, "source": fmt},
        "v2": {"label": "engrossed-in-house", "version_number": 2, "source": fmt},
    }
    assert cli["versions"] == expected
    assert cli == endpoint
    assert _example_versions(monkeypatch, tmp_path, fmt) == expected


@pytest.mark.slow
def test_an_all_digit_upload_name_is_a_label_with_no_ordinal(tmp_path):
    """Uploads named ``2026.xml`` and ``2027.xml`` keep their labels and gain no ordinal.

    #756 review: the shared resolver read a stem with no ``_`` as all prefix, so the
    endpoint published ``version_number`` 2026 and headed the report
    "v2026: 2026 → v2027: 2027". Develop answered null here. The identity is spelled
    out, and the rendered line is checked, because both surfaces agreeing proves
    nothing when they share the rule that was wrong.
    """
    from fastapi.testclient import TestClient

    from web.app import app

    old, new = tmp_path / "2026.xml", tmp_path / "2027.xml"
    shutil.copyfile(BILL_DIR / f"{V1_STEM}.xml", old)
    shutil.copyfile(BILL_DIR / f"{V2_STEM}.xml", new)

    expected = {
        "v1": {"label": "2026", "version_number": None, "source": "xml"},
        "v2": {"label": "2027", "version_number": None, "source": "xml"},
    }
    assert _endpoint_json(old, new)["versions"] == expected
    assert json.loads(_cli_json(tmp_path, old, new))["versions"] == expected

    with open(old, "rb") as start, open(new, "rb") as end:
        response = TestClient(app).post(
            "/api/compare?format=xml&output=html",
            files={"start_file": (old.name, start, "text/xml"), "end_file": (new.name, end, "text/xml")},
        )
    assert response.status_code == 200, response.text
    versions_line = response.text.split('<div class="versions">', 1)[1].split("</div>", 1)[0]
    assert versions_line.startswith("2026 &rarr; 2027"), versions_line


@pytest.mark.slow
def test_the_command_output_validates_against_the_published_schema(tmp_path, unprefixed_pair):
    """The parity test says the two agree; this says what they agree on is the contract."""
    jsonschema = pytest.importorskip("jsonschema")

    old, new = unprefixed_pair
    jsonschema.validate(json.loads(_cli_json(tmp_path, old, new)), json.loads(SCHEMA.read_text()))
