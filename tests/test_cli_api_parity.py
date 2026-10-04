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
    """The fixture pair copied under stems carrying no ``<n>_`` legislative ordinal.

    The two surfaces derive a version's identity from the filename by different
    algorithms: ``version_stems.label_from_stem`` strips a numeric prefix and
    ``version_number_from_stem`` reads the ordinal off it, while
    ``web/app.py::_label_from_filename`` strips only the path and the extension and has
    no ordinal to read at all. On ``1_reported-in-house.xml`` they therefore disagree,
    and that disagreement is #692 (one bill pair, three different version headings), a
    property of the two label algorithms rather than of the diff.

    Removing the prefix removes that variable, so the parity gate below measures the
    document rather than re-measuring #692. What the corpus filenames *do* change is
    asserted separately, so the exclusion stays one named key wide.
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


@pytest.mark.slow
def test_only_the_version_identity_depends_on_the_filename(tmp_path, corpus_pair):
    """On the committed corpus stems, ``versions`` is the only key that may differ.

    The mutation this catches and the test above cannot: making any *other* canonical
    field depend on the filename stem. That gate matters here specifically, because
    version identity is re-derived from a filename in six places (#692) and the pair
    above is chosen to make two of them agree.

    Deliberately not an inequality assertion on ``versions``: when #692 lands and the
    two algorithms converge, this test should stay green rather than pin the defect.
    """
    old, new = corpus_pair
    cli = json.loads(_cli_json(tmp_path, old, new))
    endpoint = _endpoint_json(old, new)

    assert set(cli) == set(endpoint)
    assert {k: v for k, v in cli.items() if k != "versions"} == {k: v for k, v in endpoint.items() if k != "versions"}


@pytest.mark.slow
def test_the_command_output_validates_against_the_published_schema(tmp_path, unprefixed_pair):
    """The parity test says the two agree; this says what they agree on is the contract."""
    jsonschema = pytest.importorskip("jsonschema")

    old, new = unprefixed_pair
    jsonschema.validate(json.loads(_cli_json(tmp_path, old, new)), json.loads(SCHEMA.read_text()))
