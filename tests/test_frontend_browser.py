"""Browser-level (Playwright) tests for the static front-end (#69).

These cover what TestClient can't: real browser runtime behavior. #41 was a
browser-only bug — a sample-report popup opened on page load (outside a user
gesture) was blocked, stranding the user on the upload form. Only a real
browser reproduces popup-block semantics.

Marked ``browser``; excluded from the default suite. Run with::

    uv run playwright install chromium   # one-time, downloads the browser
    uv run pytest -m browser

Skipped entirely if Playwright or its browser binary isn't available, so the
default ``-m "not browser"`` run never depends on them.
"""

from __future__ import annotations

import socket
import threading
from contextlib import closing

import pytest

from tests.corpus_paths import PROJECT_ROOT

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

pytestmark = pytest.mark.browser


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_url():
    """Serve the real app on an ephemeral port for the duration of the module.

    A browser can't use Starlette's in-process TestClient, so we run uvicorn in
    a background thread and tear it down after. Importing here (not at module
    top) keeps collection cheap when the marker is deselected.
    """
    import uvicorn

    from web.app import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to accept connections before handing the URL out.
    deadline_attempts = 100
    for _ in range(deadline_attempts):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                break
        threading.Event().wait(0.05)
    else:
        raise RuntimeError("uvicorn did not start in time")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def chromium(request):
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            yield b
            b.close()
    except Exception as exc:  # browser binary not installed, etc.
        # CI's dedicated browser step passes --run-browser: there a launch failure
        # must fail the run, not skip into a green no-op (#599). Default tier skips.
        if request.config.getoption("--run-browser"):
            raise
        pytest.skip(f"Chromium unavailable (run 'playwright install chromium'): {exc}")


def _render_report_with_toc() -> str:
    """A standalone page pairing the report's real stylesheet with a real TOC group.

    Pulls the renderer's embedded `<style>` from an actual rendered report (so the
    test tracks current CSS, not a committed artifact) and drops in the real
    `_build_tree_nav` markup for one single-line title — making
    `.tree-group > summary` exactly one text line when laid out correctly. Built
    directly rather than through the full report so the full-text gating
    (`full_text.v2`) isn't needed.
    """
    import re

    from deltatrack.diff_pdf import PdfDiff
    from deltatrack.formatters.canonical import pdf_diff_to_canonical
    from deltatrack.formatters.diff_html import _build_tree_nav, format_diff_html

    canonical = pdf_diff_to_canonical(PdfDiff(hunks=()), bill_type="hr", bill_number=8752, congress=118)
    full_report = format_diff_html(canonical)
    style = re.search(r"<style>.*?</style>", full_report, re.DOTALL).group(0)
    # A labeled parent with one labeled child is the shape that renders a
    # <details class="tree-group"> toggle; a childless node renders a plain leaf.
    full_text = "Title I\nSEC. 101."
    tree = [
        {
            "label": "Title I",
            "full_text_span": {"start": 0, "end": 7},
            "children": [{"label": "SEC. 101.", "full_text_span": {"start": 8, "end": 17}, "children": []}],
        }
    ]
    tree = _build_tree_nav(tree, full_text)
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>{style}</head>"
        f"<body><div class='sidebar'>{tree}</div></body></html>"
    )


def test_toc_group_caret_sits_on_header_line(chromium, tmp_path):
    """The full-text TOC caret and its header share one line, not stacked (#52).

    Regression guard for the `.tree-group > summary` layout. With the pre-fix
    `display: list-item`, the `::before` caret took its own line and pushed the
    header down by ~one line-height; the flex fix keeps the header on the
    caret's row. We assert the header text begins within one line-height of the
    summary's top (i.e. on the first line), which fails on the stacked layout.
    """
    report = tmp_path / "toc_report.html"
    report.write_text(_render_report_with_toc(), encoding="utf-8")

    page = chromium.new_page(viewport={"width": 1280, "height": 900})
    page.goto(report.as_uri(), wait_until="domcontentloaded")

    metrics = page.evaluate(
        """() => {
            const sum = document.querySelector('.tree-group > summary');
            if (!sum) return null;
            const box = sum.getBoundingClientRect();
            // Range over the summary's own content (the <a>), excluding ::before,
            // gives where the header text actually starts.
            const r = document.createRange();
            r.selectNodeContents(sum);
            const textTop = r.getBoundingClientRect().top;
            const cs = getComputedStyle(sum);
            let lh = parseFloat(cs.lineHeight);
            if (Number.isNaN(lh)) lh = parseFloat(cs.fontSize) * 1.2;
            return {offset: textTop - box.top, lineHeight: lh};
        }"""
    )
    page.close()

    assert metrics is not None, "no .tree-group > summary rendered"
    # On the stacked (buggy) layout the header starts a full line below the
    # caret; on the fixed layout it starts at the summary's top padding.
    assert metrics["offset"] < metrics["lineHeight"], (
        f"TOC header starts {metrics['offset']:.1f}px below the summary top "
        f"(>= one line-height {metrics['lineHeight']:.1f}px): caret and header are stacked"
    )


def _render_grouped_report() -> str:
    """A full standalone report whose cards group under tree nodes (#172).

    Hand-built canonical (the consumed contract) with a v2 tree and change
    spans in one offset space, so the own-span join is live: one ADDED change
    directly under TITLE I, two MODIFIED changes under its SALARIES /
    OPERATIONS accounts. Exercises the real _JS.

    The change types are load-bearing: the Structural filter selects on
    ``data-type !== 'modified'``, so this shape gives it exactly one survivor,
    directly under the parent node. That is what makes the filter tests below
    prove a *recount* of a nested subtree rather than a blanket hide. Before
    #671 the same shape was expressed with the Financial filter and one card
    carrying amount entries, a field the canonical no longer has.
    """
    from deltatrack.formatters.diff_html import format_diff_html

    def node(label, level, span, children=()):
        return {
            "label": label,
            "level": level,
            "own_amounts": [],
            "full_text_span": span,
            "children": list(children),
        }

    def change(i, start, end, change_type="modified"):
        return {
            "id": f"c{i}",
            "change_type": change_type,
            "section_number": "",
            "path": {"v1": ["TITLE I"], "v2": ["TITLE I"]},
            "location": None,
            "anchor_resolution": "resolved",
            # The appended token survives word_diff as ONE contiguous <ins>
            # text node, so the find bar (which scans text nodes) can hit it.
            "text": {"old": f"shared {i}", "new": f"shared {i} added{i}"},
            "move": None,
            "full_text_span": {"v1": None, "v2": {"start": start, "end": end}},
        }

    tree_v2 = [
        node(
            "TITLE I",
            "title",
            {"start": 0, "end": 7},
            [
                node("SALARIES", "account", {"start": 10, "end": 50}),
                node("OPERATIONS", "account", {"start": 60, "end": 100}),
            ],
        ),
    ]
    canonical = {
        "schema_version": "3.0",
        "bill": {"type": "hr", "number": 1, "congress": 119},
        "versions": {
            "v1": {"label": "v1", "version_number": 1, "source": "xml"},
            "v2": {"label": "v2", "version_number": 2, "source": "xml"},
        },
        "summary": {"added": 1, "removed": 0, "modified": 2, "moved": 0},
        "changes": [
            change(0, 2, 5, "added"),  # TITLE I direct
            change(1, 20, 30),  # SALARIES
            change(2, 70, 80),  # OPERATIONS
        ],
        "full_text": {"v1": "x" * 120, "v2": "TITLE I\n" + "y" * 112},
        "tree": {"v1": [], "v2": tree_v2},
    }
    return format_diff_html(canonical)


def _render_full_bill_report() -> str:
    """A standalone report whose full-text view interleaves headings and changes.

    ``_render_grouped_report``'s full text is two lines, so every tree node
    anchors to the same row — too coarse to say which change follows which
    heading. Here each TITLE is its own row with one change in the body row
    below it, and TITLE IV deliberately has no change after it, which is the
    case where "first change at or after this heading" has no answer.
    """
    from deltatrack.formatters.diff_html import format_diff_html

    lines = [
        "TITLE I",
        "alpha aaa",
        "TITLE II",
        "beta bbb",
        "TITLE III",
        "gamma ccc",
        "TITLE IV",
        "delta ddd",
    ]
    v2_text = "\n".join(lines)
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1

    def title_node(label, i):
        # Span starts on the heading row itself, so _node_anchor_offset resolves
        # the TOC link to that row. No children: renders as a clickable leaf.
        return {
            "label": label,
            "level": "title",
            "own_amounts": [],
            "full_text_span": {"start": offsets[i], "end": offsets[i] + len(lines[i])},
            "children": [],
        }

    def change(i, line_index):
        start = offsets[line_index]
        return {
            "id": f"c{i}",
            "change_type": "modified",
            "section_number": "",
            "path": {"v1": ["TITLE I"], "v2": ["TITLE I"]},
            "location": None,
            "anchor_resolution": "resolved",
            "text": {"old": f"shared {i}", "new": f"shared {i} added{i}"},
            "move": None,
            "full_text_span": {
                "v1": None,
                "v2": {"start": start, "end": start + len(lines[line_index])},
            },
        }

    canonical = {
        "schema_version": "3.0",
        "bill": {"type": "hr", "number": 1, "congress": 119},
        "versions": {
            "v1": {"label": "v1", "version_number": 1, "source": "xml"},
            "v2": {"label": "v2", "version_number": 2, "source": "xml"},
        },
        "summary": {"added": 0, "removed": 0, "modified": 3, "moved": 0},
        # One change in each of the first three titles' body rows; none under
        # TITLE IV.
        "changes": [change(0, 1), change(1, 3), change(2, 5)],
        "full_text": {"v1": "x" * len(v2_text), "v2": v2_text},
        "tree": {
            "v1": [],
            "v2": [title_node(label, i) for i, label in enumerate(lines) if i % 2 == 0],
        },
    }
    return format_diff_html(canonical)


def test_counter_follows_full_bill_navigation(chromium, tmp_path):
    """In the full-text view, an explicit jump sets the prev/next position the
    same way it does in the changes view (#185).

    The two entry points differ from the changes view and need different
    resolution. An inline highlight IS a nav target, so it resolves exactly, as
    a card does. A TOC link points at a heading row, which is never a target, so
    it resolves to the first change at or after that row ("the next change from
    here down"); a heading with nothing below it leaves the position alone
    rather than guessing.
    """
    report = tmp_path / "full_bill_nav.html"
    report.write_text(_render_full_bill_report(), encoding="utf-8")
    page = chromium.new_page(viewport={"width": 1280, "height": 900})
    page.goto(report.as_uri(), wait_until="domcontentloaded")

    page.locator('.view-toggle__btn[data-view="full"]').click()
    counter = page.locator("#nav-counter")
    assert counter.inner_text() == "0 / 3"

    # 1. TOC jump to a heading resolves to the first change below it, and the
    # arrow steps on from there (pre-fix this read "0 / 3" then "1 / 3").
    # TITLE II is row 18; the next change is c1 at 27, the 2nd of 3.
    page.locator('.sidebar-tree a[href="#fb-off-18"]').click()
    assert counter.inner_text() == "2 / 3"
    page.locator("#btn-next").click()
    assert counter.inner_text() == "3 / 3"

    # Resolution is "at or after", not "the nearest": TITLE I is row 0 and the
    # first change is c0 at row 8, so it lands on 1 rather than staying put.
    page.locator('.sidebar-tree a[href="#fb-off-0"]').click()
    assert counter.inner_text() == "1 / 3"

    # 2. A click on an inline highlight is itself a target, so it resolves
    # exactly, the way a change card does.
    page.locator("#attr-c2").click()
    assert counter.inner_text() == "3 / 3"
    page.keyboard.press("ArrowLeft")
    assert counter.inner_text() == "2 / 3"

    # 3. A heading with no change below it has no answer: the position is left
    # where it was rather than being reset or clamped. TITLE IV is the last row
    # and every change sits above it.
    page.locator('.sidebar-tree a[href="#fb-off-56"]').click()
    assert counter.inner_text() == "2 / 3"

    # The changes view keeps its own exact-match resolution: switching views
    # resets, and a card jump still lands on that card and not a neighbour.
    page.locator('.view-toggle__btn[data-view="changes"]').click()
    assert counter.inner_text() == "0 / 3"
    page.locator("#change-2").click(position={"x": 5, "y": 5})
    assert counter.inner_text() == "3 / 3"
    page.close()


def test_filtering_hides_empty_card_groups_and_updates_nav_counts(chromium, tmp_path):
    """Structural filter empties the account groups: their change-group headings
    hide, and the TITLE I nav-group count recounts to the visible subtree (#172).
    Browser-level because the contract lives in applyFilters' runtime behavior,
    which string-level _JS assertions can't prove."""
    report = tmp_path / "grouped.html"
    report.write_text(_render_grouped_report(), encoding="utf-8")
    page = chromium.new_page(viewport={"width": 1280, "height": 900})
    page.goto(report.as_uri(), wait_until="domcontentloaded")

    groups = page.locator(".change-group")
    assert groups.count() == 3  # TITLE I + two nested accounts
    title_count = page.locator(".nav-group__count").first
    assert title_count.inner_text() == "(3)"

    page.locator('input[name="change-filter"][value="structural"]').check()
    # The two account groups hold only `modified` cards -> hidden; the TITLE I
    # group keeps its direct `added` card and its count recounts.
    assert page.locator(".change-group:visible").count() == 1
    assert title_count.inner_text() == "(1)"
    assert page.locator("#change-0").is_visible()
    assert page.locator("#change-1").is_hidden()
    page.close()


def test_prev_next_steps_into_nested_groups_and_reveals_collapsed(chromium, tmp_path):
    """Prev/next reaches cards inside nested open groups with a true counter,
    and stepping to a card inside a user-collapsed group re-opens the group
    rather than scrolling to an invisible card; likewise a sidebar link into a
    collapsed group (#172). (Modern Chromium keeps closed-details content in
    the layout tree — content-visibility, not display:none — so collapsed
    cards stay in the target set; revealCard makes reaching them work.)"""
    report = tmp_path / "grouped_nav.html"
    report.write_text(_render_grouped_report(), encoding="utf-8")
    page = chromium.new_page(viewport={"width": 1280, "height": 900})
    page.goto(report.as_uri(), wait_until="domcontentloaded")

    counter = page.locator("#nav-counter")
    assert counter.inner_text() == "0 / 3"
    nxt = page.locator("#btn-next")
    for expected in ("1 / 3", "2 / 3", "3 / 3"):
        nxt.click()
        assert counter.inner_text() == expected

    # Collapse the SALARIES group, then step back to the card inside it:
    # revealCard must re-open the group so the card is actually shown.
    salaries = page.locator(".change-group .change-group").first
    salaries.locator("> summary").click()
    assert salaries.evaluate("el => el.open") is False
    page.locator("#btn-prev").click()
    assert salaries.evaluate("el => el.open") is True
    assert page.locator("#change-1").is_visible()

    # Same via a sidebar link into a re-collapsed group (fragment navigation
    # into a closed <details> doesn't auto-expand in every browser). Collapse
    # via JS here: after the scroll the summary can sit under the sticky
    # header, where a UI click never becomes actionable (the click-collapse
    # path is already exercised above).
    salaries.evaluate("el => el.open = false")
    assert salaries.evaluate("el => el.open") is False
    # Sidebar nav groups are collapsed by default; expand down to the link.
    page.locator(".sidebar .nav-group > summary").first.click()
    page.locator(".sidebar .nav-group .nav-group > summary").first.click()
    page.locator('.sidebar a[href="#change-1"]').click()
    assert salaries.evaluate("el => el.open") is True
    assert page.locator("#change-1").is_visible()

    # Find-bar stepping is the third navigation mechanism: a hit inside a
    # collapsed group must re-open it too ("added1" only occurs in change-1).
    # runFind is debounced 150ms on input, then lands on the first hit itself;
    # wait for the mark rather than racing the debounce.
    salaries.evaluate("el => el.open = false")
    page.locator("#find-input").fill("added1")
    page.locator("mark.find-hit--current").wait_for(state="attached")
    assert salaries.evaluate("el => el.open") is True
    assert page.locator("mark.find-hit--current").is_visible()
    page.close()


def test_counter_follows_explicit_card_navigation(chromium, tmp_path):
    """Jumping to a card by any explicit gesture sets the prev/next position to
    that card, so the next arrow step continues from what the reader is looking
    at rather than from wherever the arrows last were (#185).

    Two entry points: sidebar nav links and a click on the card itself (which is
    what makes the scroll-and-read flow work). The index is taken against the
    currently visible targets, so it has to stay correct with the type filter
    active. Browser-level because the contract is the runtime index lookup,
    which string assertions can't prove.

    A third entry point, Financial Summary row links, was removed with the table
    in #671 — along with the case where a jump target outlives its own card,
    which only the table could produce (applyFilters hides cards and their
    sidebar nav items together, so no surviving gesture points at a hidden card).
    """
    report = tmp_path / "grouped_sync.html"
    report.write_text(_render_grouped_report(), encoding="utf-8")
    page = chromium.new_page(viewport={"width": 1280, "height": 900})
    page.goto(report.as_uri(), wait_until="domcontentloaded")

    counter = page.locator("#nav-counter")
    assert counter.inner_text() == "0 / 3"

    # Sidebar nav groups are collapsed by default; expand down to the link.
    page.locator(".sidebar .nav-group > summary").first.click()
    page.locator(".sidebar .nav-group .nav-group > summary").first.click()

    # 1. Sidebar link into a nested group: counter lands on that card, and the
    # next arrow step continues from it (pre-#185 this read "0 / 3" then "1 / 3").
    page.locator('.sidebar a[href="#change-1"]').click()
    assert counter.inner_text() == "2 / 3"
    page.locator("#btn-next").click()
    assert counter.inner_text() == "3 / 3"

    # 2. A click on the card body itself, then keyboard stepping from there.
    page.locator("#change-1").click(position={"x": 5, "y": 5})
    assert counter.inner_text() == "2 / 3"
    page.keyboard.press("ArrowRight")
    assert counter.inner_text() == "3 / 3"

    # The index is against the *visible* targets: with the Structural filter on,
    # change-0 (the one `added` card) is the only target, so jumping to it is
    # 1 / 1, not 1 / 3.
    page.locator('input[name="change-filter"][value="structural"]').check()
    assert counter.inner_text() == "0 / 1"
    page.locator("#change-0").click(position={"x": 5, "y": 5})
    assert counter.inner_text() == "1 / 1"

    # A jump into a collapsed group still resolves to the right index: the card
    # is revealed first, so it is a visible target when indexOf runs.
    page.locator('input[name="change-filter"][value="all"]').check()
    salaries = page.locator(".change-group .change-group").first
    salaries.evaluate("el => el.open = false")
    page.locator('.sidebar a[href="#change-1"]').click()
    assert salaries.evaluate("el => el.open") is True
    assert counter.inner_text() == "2 / 3"
    page.close()


def test_sample_report_opens_in_new_tab(live_url, chromium):
    """Clicking "View a sample report" opens the report in a new tab (#41).

    Pre-#41 this link routed through a page-load window.open that the browser
    blocked. The fix makes it a direct target=_blank link, so the click itself
    is the gesture that opens the tab. We assert a second page opens and shows
    the diff report — not that we're stranded on the upload form.
    """
    page = chromium.new_page()
    page.goto(live_url, wait_until="domcontentloaded")

    with page.context.expect_page() as new_page_info:
        page.get_by_role("link", name="View a sample report").click()
    report = new_page_info.value
    report.wait_for_load_state("domcontentloaded")

    # The new tab is the standalone diff report, not the upload page bouncing back.
    assert "example.html" in report.url
    assert "H.R. 8752" in report.title()

    # And the landing page shows no "pop-up blocked" / load error.
    assert page.locator("#upload-error").is_hidden()

    report.close()
    page.close()


# --- The upload flow, in a real browser (#71) --------------------------------
#
# Everything the upload page does between "choose a file" and "the report is on
# screen" is compare.js at runtime: the pre-flight validation, the format
# toggle, the button's enabled state, and the report tab itself. The server-side
# guards are covered by TestClient (tests/test_pdf_compare.py), which cannot see
# any of the above — it never runs the script. These tests therefore assert only
# runtime behavior, and deliberately do not re-assert what the server rejects.

#: A committed bill XML (a 2.6 KB joint resolution), used as the start version so
#: the XML path runs against the shape the engine actually sees.
_XML_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "resolutions" / "BILLS-119hjres25ih.xml"
#: The end version is this fixture with its one operative clause reworded. The
#: repo's other committed version pairs of a single resolution diff to zero
#: changes, which would leave the report with no card to assert on — and a test
#: that asserts nothing changed cannot tell a working upload from a broken one.
_XML_EDIT = (b"Congress disapproves the rule", b"Congress approves the amended rule")

# One appropriation, with the amount changed between the two versions, so the
# report has something to show and the test can name what it expects to see.
_PDF_BODY_START = [
    "For necessary expenses of the Office of Investigation, $12,345,000, to remain",
    "available until September 30, 2027: Provided, That not to exceed $50,000 shall",
    "be for official reception and representation expenses.",
]
_PDF_BODY_END = [
    "For necessary expenses of the Office of Investigation, $99,999,000, to remain",
    "available until September 30, 2027: Provided, That not to exceed $50,000 shall",
    "be for official reception and representation expenses.",
]


#: Lines a document needs before the engine will judge its layout at all
#: (``compare.pdf._MIN_LINES_FOR_GUARD``); read from the engine so the unnumbered
#: pair below stays over the threshold if the threshold moves.
def _decline_threshold():
    from deltatrack.compare.pdf import _MIN_LINES_FOR_GUARD

    return _MIN_LINES_FOR_GUARD


def _pdf_pair(tmp_path, *, numbered=True):
    """A start/end PDF pair written to ``tmp_path``, in GPO's numbered layout.

    Generated rather than committed: the pair is a few lines of source here
    instead of two binaries in the tree, and the layout is load-bearing — the
    engine declines an unnumbered layout outright, so the line-number gutter has
    to be there for the upload to reach a report at all.

    ``numbered=False`` drops the gutter and pads the body past the length the
    engine needs before it will judge a layout, producing the pair the engine
    declines. That is how a test reaches the server-error path on purpose.
    """
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    def write(path, body):
        if not numbered:
            # Enough lines to be judged, with no gutter: an enrolled-style print.
            body = (body * (_decline_threshold() // len(body) + 2))[: _decline_threshold() + 10]
        c = canvas.Canvas(str(path), pagesize=letter)
        _, height = letter
        y = height - 60
        for i, line in enumerate(body, start=1):
            c.setFont("Times-Roman", 11)
            if numbered:
                c.drawString(54, y, str(i))  # the line-number gutter
            c.drawString(78, y, line)
            y -= 22 if numbered else 12
            if y < 60:
                c.showPage()
                y = height - 60
        c.showPage()
        c.save()
        return path

    return (
        write(tmp_path / "start.pdf", _PDF_BODY_START),
        write(tmp_path / "end.pdf", _PDF_BODY_END),
    )


def _upload_page(chromium, live_url, *, init_script=None):
    """The compare page, served by the real app. ``init_script`` runs before it."""
    page = chromium.new_page()
    if init_script:
        page.add_init_script(init_script)
    page.goto(f"{live_url}/compare.html", wait_until="domcontentloaded")
    return page


def _watch_uploads(page):
    """Collect POSTs the page makes; a blocked upload must not reach the server."""
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    return posts


def test_upload_pdf_pair_opens_the_report_in_a_new_tab(live_url, chromium, tmp_path):
    """The whole server-side path, end to end: choose two PDFs, click Compare,
    read the report (#71).

    This is the product's one interactive flow and nothing else exercised it in
    a browser. The pieces are individually covered — the API by TestClient, the
    renderer by the report tests above — but the wiring between them (the file
    reaching FormData, the response reaching a new tab as a rendered document)
    only exists at runtime.
    """
    start, end = _pdf_pair(tmp_path)
    page = _upload_page(chromium, live_url)
    btn = page.locator("#compare-btn")
    assert btn.is_disabled(), "Compare should start disabled, with no files chosen"

    # The start slot goes through the real gesture: clicking the slot is what
    # forwards to the hidden input, which is the path a user actually takes.
    with page.expect_file_chooser() as chooser:
        page.locator("#start-slot").click()
    chooser.value.set_files(start)
    assert page.locator("#start-name").inner_text() == start.name
    assert btn.is_disabled(), "one file is not a comparison"

    page.locator("#end-input").set_input_files(end)
    assert btn.is_enabled()

    with page.context.expect_page() as report_info:
        btn.click()
    report = report_info.value
    # The tab opens blank on the click and is written when the response lands,
    # so wait for the report's own markup rather than for the navigation.
    report.wait_for_selector(".change", timeout=60_000)

    # It is the diff of what was uploaded, not an empty shell: the changed
    # amount is the one thing that distinguishes these two files.
    assert "$99,999,000" in report.locator("body").inner_text()

    assert page.locator("#upload-error").is_hidden()
    assert "new tab" in page.locator("#upload-success").inner_text()
    # Back to a resting state, ready for the next pair.
    assert btn.inner_text() == "Compare"
    report.close()
    page.close()


def _xml_pair(tmp_path):
    """A start/end bill-XML pair written to ``tmp_path`` (see :data:`_XML_EDIT`)."""
    original = _XML_FIXTURE.read_bytes()
    edited = original.replace(*_XML_EDIT)
    assert edited != original, "the fixture no longer contains the clause this pair edits"
    start, end = tmp_path / "start.xml", tmp_path / "end.xml"
    start.write_bytes(original)
    end.write_bytes(edited)
    return start, end


def test_upload_xml_pair_uses_the_selected_format(live_url, chromium, tmp_path):
    """With XML selected, the same flow diffs XML — the toggle drives the
    request, not just the accept filter (#71).

    The format is a query parameter the server dispatches on, so a toggle that
    updated the UI but not the request would send bill XML to the PDF engine and
    fail late, after the upload. Asserting the report renders proves the whole
    parameter path.
    """
    start, end = _xml_pair(tmp_path)
    page = _upload_page(chromium, live_url)
    page.locator('input[name="format"][value="xml"]').check()
    page.locator("#start-input").set_input_files(start)
    page.locator("#end-input").set_input_files(end)

    with page.expect_request("**/api/compare*") as request_info:
        with page.context.expect_page() as report_info:
            page.locator("#compare-btn").click()
    assert "format=xml" in request_info.value.url

    report = report_info.value
    report.wait_for_selector(".change", timeout=60_000)
    assert "H.J.Res. 25" in report.title()
    assert "approves the amended rule" in report.locator("body").inner_text()
    assert page.locator("#upload-error").is_hidden()
    report.close()
    page.close()


def test_format_toggle_clears_chosen_files_and_repoints_the_picker(live_url, chromium, tmp_path):
    """Switching PDF↔XML forgets both files and re-points the picker (#71).

    A PDF is invalid under XML and vice versa, so a file surviving the switch
    would be submitted against the wrong engine. Clearing the native input's own
    ``value`` (not just our copy) is the part that only shows up in a browser:
    leave it set and re-picking the same file fires no ``change`` event, so the
    slot silently stays empty.
    """
    start, end = _pdf_pair(tmp_path)
    page = _upload_page(chromium, live_url)
    page.locator("#start-input").set_input_files(start)
    page.locator("#end-input").set_input_files(end)
    assert page.locator("#compare-btn").is_enabled()
    assert page.locator("#start-slot").evaluate("el => el.classList.contains('has-file')")

    page.locator('input[name="format"][value="xml"]').check()

    for which in ("start", "end"):
        assert page.locator(f"#{which}-name").inner_text() == ""
        assert not page.locator(f"#{which}-slot").evaluate("el => el.classList.contains('has-file')")
        assert page.locator(f"#{which}-input").input_value() == "", "native input kept the old file"
        assert page.locator(f"#{which}-input").get_attribute("accept") == "application/xml,text/xml,.xml"
    assert page.locator("#compare-btn").is_disabled()
    assert page.locator("#upload-note").inner_text().startswith("XML")

    # And back: the filter follows the current selection in both directions.
    page.locator('input[name="format"][value="pdf"]').check()
    assert page.locator("#start-input").get_attribute("accept") == "application/pdf,.pdf"
    assert page.locator("#upload-note").inner_text().startswith("PDF")
    page.close()


@pytest.mark.parametrize(
    ("fmt", "content", "expected"),
    [
        ("pdf", b"", "Start PDF is empty."),
        ("pdf", b"this is not a PDF at all, just some text", "Start PDF doesn't look like a PDF."),
        # A real PDF submitted while XML is selected: the wrong-format case a
        # user reaches by picking files and then flipping the toggle back.
        ("xml", b"%PDF-1.4 a real enough PDF header", "Start XML doesn't look like XML."),
    ],
    ids=["empty", "not-a-pdf", "pdf-under-xml"],
)
def test_client_side_validation_refuses_a_file_before_uploading(live_url, chromium, tmp_path, fmt, content, expected):
    """A file that fails the pre-flight check surfaces an inline error and never
    leaves the browser (#71).

    The server re-validates all of this, so the point of the client check is
    that the user finds out immediately and their file is not uploaded to be
    rejected — which is what the "no request was made" assertion pins. Without
    it this test would pass just as well against a page that uploaded
    everything and rendered the server's error.
    """
    bad = tmp_path / f"bad.{fmt}"
    bad.write_bytes(content)
    page = _upload_page(chromium, live_url)
    posts = _watch_uploads(page)
    if fmt != "pdf":
        page.locator(f'input[name="format"][value="{fmt}"]').check()
    page.locator("#start-input").set_input_files(bad)
    page.locator("#end-input").set_input_files(bad)

    page.locator("#compare-btn").click()

    error = page.locator("#upload-error")
    error.wait_for(state="visible")
    assert expected in error.inner_text()
    assert posts == [], "a file that failed the pre-flight check was uploaded anyway"
    assert len(page.context.pages) == 1, "a report tab was opened for a file that never compared"
    page.close()


def test_oversized_file_is_refused_before_uploading(live_url, chromium, tmp_path):
    """A file past the 150 MB cap is refused in the browser (#71).

    The size is stamped onto the ``File`` object rather than written to disk —
    a real 150 MB fixture would dominate the suite's runtime for one branch.
    The patch is asserted first, so a browser where it doesn't take fails here
    rather than quietly testing a small file against the wrong branch.
    """
    small = tmp_path / "big.pdf"
    small.write_bytes(b"%PDF-1.4 stands in for a very large file")
    page = _upload_page(chromium, live_url)
    posts = _watch_uploads(page)
    page.locator("#start-input").set_input_files(small)
    page.locator("#end-input").set_input_files(small)

    patched = page.evaluate(
        """() => {
            const f = document.getElementById('start-input').files[0];
            Object.defineProperty(f, 'size', {value: 200 * 1024 * 1024});
            return document.getElementById('start-input').files[0].size;
        }"""
    )
    assert patched == 200 * 1024 * 1024, "the oversize stand-in did not take; this test proves nothing"

    page.locator("#compare-btn").click()
    error = page.locator("#upload-error")
    error.wait_for(state="visible")
    assert "larger than 150 MB" in error.inner_text()
    assert posts == [], "an over-limit file was uploaded before being refused"
    page.close()


def test_compare_button_reports_progress_while_the_diff_runs(live_url, chromium, tmp_path):
    """While a diff is in flight the button says "Comparing…" and is disabled,
    then returns to Compare (#71).

    A server diff takes seconds to minutes, so with no in-flight state the page
    looks inert and invites a second click on the same pair. The response is
    held in the page — ``fetch`` is captured and released on demand — rather
    than by slowing the network, so the mid-flight state is observed at a fixed
    point instead of raced against a real request.
    """
    hold_fetch = """
        window.__release = null;
        const original = window.fetch;
        window.fetch = (...args) => new Promise((resolve, reject) => {
            window.__release = () => original(...args).then(resolve, reject);
        });
    """
    start, end = _pdf_pair(tmp_path)
    page = _upload_page(chromium, live_url, init_script=hold_fetch)
    page.locator("#start-input").set_input_files(start)
    page.locator("#end-input").set_input_files(end)

    btn = page.locator("#compare-btn")
    with page.context.expect_page() as report_info:
        btn.click()
    page.wait_for_function("() => window.__release !== null")

    assert btn.inner_text() == "Comparing…"
    assert btn.is_disabled(), "the button stays clickable while a diff is already running"

    page.evaluate("() => window.__release()")
    report = report_info.value
    report.wait_for_selector(".change", timeout=60_000)
    assert btn.inner_text() == "Compare"
    assert btn.is_enabled()
    report.close()
    page.close()


def test_report_tab_shows_progress_until_the_report_arrives(live_url, chromium, tmp_path):
    """The report tab says a diff is running, and the report replaces it (#490).

    The tab has to be opened on the click itself, since a pop-up opened later is
    blocked (#41), so it necessarily sits empty for the whole render. Left on
    about:blank that is indistinguishable from a crash, and the reader's move is
    to close it or re-click, throwing away a render that was about to finish.
    The test above covers the *button*'s in-flight state on the upload page;
    this covers the tab, which is where the reader is actually looking.

    Same held-``fetch`` trick as above, so the mid-flight state is observed at a
    fixed point rather than raced against a real request.
    """
    hold_fetch = """
        window.__release = null;
        const original = window.fetch;
        window.fetch = (...args) => new Promise((resolve, reject) => {
            window.__release = () => original(...args).then(resolve, reject);
        });
    """
    start, end = _pdf_pair(tmp_path)
    page = _upload_page(chromium, live_url, init_script=hold_fetch)
    page.locator("#start-input").set_input_files(start)
    page.locator("#end-input").set_input_files(end)

    with page.context.expect_page() as report_info:
        page.locator("#compare-btn").click()
    report = report_info.value
    page.wait_for_function("() => window.__release !== null")

    report.wait_for_selector(".spinner")
    assert "Diff in progress" in report.locator("h1").inner_text()
    # A real animation, not a static glyph: a frozen circle would read as hung.
    assert report.locator(".spinner").evaluate("el => getComputedStyle(el).animationName") != "none"

    # And the placeholder is replaced by the report rather than left alongside it.
    page.evaluate("() => window.__release()")
    report.wait_for_selector(".change", timeout=60_000)
    assert report.locator(".spinner").count() == 0
    report.close()
    page.close()


def test_blocked_popup_is_reported_instead_of_failing_silently(live_url, chromium, tmp_path):
    """When the browser blocks the report tab, the user is told (#71).

    This is the #41 failure mode on the upload path: the report has nowhere to
    go, and without the message the click appears to do nothing. The tab is
    opened *before* the upload, so a blocked pop-up must also abort before the
    files leave the browser — asserted here because "nothing was sent" is not
    visible on screen.
    """
    start, end = _pdf_pair(tmp_path)
    page = _upload_page(chromium, live_url, init_script="window.open = () => null;")
    posts = _watch_uploads(page)
    page.locator("#start-input").set_input_files(start)
    page.locator("#end-input").set_input_files(end)

    page.locator("#compare-btn").click()

    error = page.locator("#upload-error")
    error.wait_for(state="visible")
    assert "Pop-up blocked" in error.inner_text()
    assert posts == [], "files were uploaded for a report that had nowhere to open"
    assert page.locator("#upload-success").is_hidden()
    page.close()


def test_a_server_rejection_is_shown_and_the_blank_tab_is_closed(live_url, chromium, tmp_path):
    """When the server refuses the pair, the reader gets its reason and no empty
    tab is left behind (#71).

    This is the one path the pre-flight checks cannot reach: the files are
    well-formed enough to send, and the refusal comes back after the report tab
    has already been opened. Two things have to happen, and neither is visible
    from the API tests — the server's own wording has to reach the page (it is
    written for the reader, so ``detail`` is rendered rather than "HTTP 422"),
    and the tab opened in advance has to be closed rather than stranded on its
    in-progress placeholder. The 422 here is the engine declining an unnumbered layout
    (#141); the rate-limit and timeout replies come back through the same
    branch.
    """
    start, end = _pdf_pair(tmp_path, numbered=False)
    page = _upload_page(chromium, live_url)
    page.locator("#start-input").set_input_files(start)
    page.locator("#end-input").set_input_files(end)

    page.locator("#compare-btn").click()

    error = page.locator("#upload-error")
    error.wait_for(state="visible")
    # The engine's own sentence, not a status code: the generic fallback in
    # compare.js would read "Request failed (HTTP 422)".
    assert "no printed line numbers" in error.inner_text()
    assert len(page.context.pages) == 1, "the report tab was left stranded on the placeholder"
    assert page.locator("#upload-success").is_hidden()
    # And the form is usable again for the next pair.
    assert page.locator("#compare-btn").inner_text() == "Compare"
    assert page.locator("#compare-btn").is_enabled()
    page.close()


# --- Find across printed line breaks (#162) ---------------------------------
#
# The PDF full-text view is print-faithful: GPO's line breaks and its soft-
# hyphenated word splits are real DOM boundaries, one `.full-text-line` per printed
# line. Matching per text node therefore made every printed line an island, so
# a phrase the reader can see on screen returned `0 / 0`.

# Printed page text in GPO's `<line number> <content>` layout. Deliberately
# contains all four boundary cases the find bar has to cross (or refuse to):
#   line 1/2   soft hyphen, lowercase continuation  -> join ("Services")
#   line 2/3   plain wrap, no hyphen                -> join with a space
#   line 4/5   real compound, uppercase continuation -> must NOT join
#   line 6     column padding (a run of spaces)      -> collapses to one space
_FIND_PAGE_SRC = (
    "1 for expenses of the Administrator of General Serv-\n"
    "2 ices for vehicles the Administrator of General\n"
    "3 Services does not provide for lease under this\n"
    "4 heading, and for grants under the Child-\n"
    "5 Rescue Act of 2019, to remain available until\n"
    "6 expended                                    $5,000,000\n"
)


def _find_fixture_texts() -> tuple[str, str]:
    """(printed display text, merged whole-word text) from the real parser.

    Both come from the producer the browser has to agree with — `pdf_full_text`
    is the de-hyphenated ground truth the flattened search string must
    reproduce, so the fixture can't encode a belief about GPO's line-joining
    that the parser doesn't share.
    """
    from deltatrack.parsers.pdf_text import (
        Page,
        _merge_print_lines,
        _parse_print_lines,
        pdf_full_text,
        pdf_full_text_print,
    )

    print_lines = _parse_print_lines(_FIND_PAGE_SRC.rstrip("\n"))
    merged, ranges = _merge_print_lines(print_lines)
    page = Page(1, tuple(merged), tuple(print_lines), tuple(ranges))
    printed_text, _ = pdf_full_text_print([page])
    merged_text, _ = pdf_full_text([page])
    return printed_text, merged_text


def _render_find_report() -> str:
    """A real report whose full-text view is the print-faithful page above.

    One change span sits mid-line on the word "vehicles", so that row's text is
    split across sibling text nodes by the tracked-change mark — the in-line
    counterpart of the cross-row case.
    """
    from deltatrack.formatters.diff_html import format_diff_html

    printed_text, _ = _find_fixture_texts()
    start = printed_text.index("vehicles")
    canonical = {
        "schema_version": "3.0",
        "bill": {"type": "hr", "number": 8752, "congress": 118},
        "versions": {
            "v1": {"label": "Reported", "version_number": 1, "source": "pdf"},
            "v2": {"label": "Enrolled", "version_number": 2, "source": "pdf"},
        },
        "summary": {"added": 0, "removed": 0, "modified": 1},
        "full_text": {"v1": "", "v2": printed_text},
        "changes": [
            {
                "id": "c0",
                "change_type": "modified",
                "section_number": "",
                "path": {"v1": [], "v2": []},
                "location": None,
                "anchor_resolution": "resolved",
                "text": {"old": "cars", "new": "vehicles"},
                "move": None,
                "full_text_span": {"v1": None, "v2": {"start": start, "end": start + len("vehicles")}},
            }
        ],
    }
    return format_diff_html(canonical)


def _open_full_bill(chromium, tmp_path, name="find_report.html"):
    report = tmp_path / name
    report.write_text(_render_find_report(), encoding="utf-8")
    page = chromium.new_page(viewport={"width": 1280, "height": 900})
    page.goto(report.as_uri(), wait_until="domcontentloaded")
    page.locator('.view-toggle__btn[data-view="full"]').click()
    return page


def _find(page, query):
    """Type a query and settle the 150ms debounce; returns the counter text."""
    page.locator("#find-input").fill(query)
    page.wait_for_timeout(300)
    return page.locator("#find-counter").inner_text()


def test_find_matches_across_printed_line_breaks(chromium, tmp_path):
    """Phrases that wrap across printed lines are findable (#162).

    Pre-fix each of these returned `0 / 0` — the silent failure mode, since the
    reader can see the phrase on screen.
    """
    page = _open_full_bill(chromium, tmp_path)

    # Soft hyphen: the page reads "Serv-" / "ices" across two rows.
    assert _find(page, "General Services") == "1 / 2"
    # Plain wrap, no hyphen involved: "…of General" / "Services does not…".
    assert _find(page, "Administrator of General Services does not") == "1 / 1"
    # A match crossing a tracked-change mark inside ONE printed line: the <ins>
    # around "vehicles" splits that row into sibling text nodes.
    assert _find(page, "for vehicles the") == "1 / 1"
    page.close()


def test_find_does_not_join_real_compounds(chromium, tmp_path):
    """The soft-hyphen rejoin must not weld a real compound together.

    GPO breaks syllables with a lowercase continuation; "Child-" / "Rescue"
    keeps its uppercase continuation and is a real hyphenated name. Without this
    the rejoin looks correct on every positive case while silently corrupting
    compounds — the join would fire, but wrongly.
    """
    page = _open_full_bill(chromium, tmp_path)
    assert _find(page, "childrescue") == "0 / 0"
    assert _find(page, "Child-Rescue Act") == "1 / 1"
    page.close()


def test_find_ignores_line_number_gutter(chromium, tmp_path):
    """Line numbers are print furniture, not bill text, so they aren't searched.

    Flattening the view would otherwise splice the gutter into the middle of the
    text ("…Serv- 2 ices…"), both creating false hits and breaking real ones.
    """
    page = _open_full_bill(chromium, tmp_path)
    # A phrase crossing rows 5 -> 6: with the gutter in the searchable text this
    # would read "…available until 6 expended" and never match. This is the
    # assertion that makes the test discriminating — the two negatives below
    # both hold on the pre-fix per-node search as well.
    assert _find(page, "available until expended") == "1 / 1"
    # Runs of column padding collapse to a single space, as they do in the
    # parser's merged text.
    assert _find(page, "expended $5,000,000") == "1 / 1"
    # The rejoined hyphen is gone from the searchable text, and no gutter digit
    # is ever itself a hit.
    assert _find(page, "Serv- ices") == "0 / 0"
    hits_in_gutter = page.evaluate("() => document.querySelectorAll('.full-text-line__number mark.find-hit').length")
    assert hits_in_gutter == 0
    page.close()


def test_find_counts_matches_not_marks_and_steps_across_rows(chromium, tmp_path):
    """A match spanning two rows is one hit, and stepping lands on its first row.

    The match is highlighted with one <mark> per row it covers; counting marks
    would report a two-row phrase as two separate hits and make prev/next step
    through half-matches.
    """
    page = _open_full_bill(chromium, tmp_path)
    assert _find(page, "General Services") == "1 / 2"

    marks = page.evaluate("() => document.querySelectorAll('mark.find-hit--current').length")
    assert marks == 2, "the two-row match should carry current styling on both of its marks"

    # Both marks belong to the current hit, and the first sits on the earlier row.
    rows = page.evaluate(
        """() => Array.from(document.querySelectorAll('mark.find-hit--current'))
                     .map(m => m.closest('.full-text-line')
                                .querySelector('.full-text-line__number').textContent.trim())"""
    )
    assert rows == ["1", "2"]

    page.locator("#find-next").click()
    assert page.locator("#find-counter").inner_text() == "2 / 2"
    page.close()


def test_find_agrees_with_the_parser_merged_text(chromium, tmp_path):
    """Every phrase in the parser's merged text is findable in the browser.

    The JS rejoin re-implements one parser rule (`_merge_print_lines`), so the
    two can drift. Rather than assert the flattened string against a
    hand-written expectation — which would only encode a belief about what the
    parser does — this walks every 5-word window of `pdf_full_text`'s output,
    the de-hyphenated ground truth, and requires the browser to find each one.
    A missing join, a wrong join, or a gutter spliced into the text all break
    some window.
    """
    import re as _re

    _, merged_text = _find_fixture_texts()
    # Windows stay inside one merged line. Each merged line is already whole-word
    # (the parser rejoined its soft hyphens), so this pins the de-hyphenation
    # contract without asserting how the JS joins one display line to the next.
    windows = []
    for line in merged_text.splitlines():
        words = _re.sub(r"\s+", " ", line[7:]).strip().split(" ")
        windows += [" ".join(words[i : i + 5]) for i in range(0, len(words) - 4)]
    assert len(windows) > 10, "fixture too small to be evidence of anything"

    page = _open_full_bill(chromium, tmp_path)
    missing = [w for w in windows if _find(page, w) == "0 / 0"]

    # The check must be able to fail: a phrase the merged text does NOT contain
    # has to come back empty, otherwise a green run above proves nothing.
    control = _find(page, "vehicles for grants under expended")
    page.close()

    assert not missing, f"phrases in the merged text that Find cannot locate: {missing}"
    assert control == "0 / 0", "control phrase matched — the search is not discriminating"


def test_find_does_not_match_across_a_deletion_and_its_replacement(chromium, tmp_path):
    """In the changes view, old and new wording are alternatives, not a sequence.

    A card shows the removed text next to the text that replaces it. Flattening
    the view for search puts them side by side, so joining them with a space
    would let a query match wording that appears in no version of the bill —
    a false positive, which is worse than the missed match this all started
    with. A separator no query can contain keeps them apart.
    """
    report = tmp_path / "changes_view.html"
    report.write_text(_render_find_report(), encoding="utf-8")
    page = chromium.new_page(viewport={"width": 1280, "height": 900})
    page.goto(report.as_uri(), wait_until="domcontentloaded")  # changes view is the default

    # The card reads "cars" (removed) immediately followed by "vehicles" (added).
    card = page.locator(".change").first.inner_text()
    assert "cars" in card and "vehicles" in card, "fixture no longer shows both sides"

    assert _find(page, "cars vehicles") == "0 / 0"
    # Each side is still findable on its own.
    assert _find(page, "vehicles") != "0 / 0"
    assert _find(page, "cars") != "0 / 0"
    page.close()


def test_find_accepts_a_query_pasted_off_the_screen(chromium, tmp_path):
    """A phrase copied from the page can end at a line-break hyphen.

    The reader sees "…Administrator of General Serv-" at the end of a printed
    line and pastes exactly that. The hyphen is not in the searchable text
    (it was rejoined away), so without handling this the query returns 0 / 0 —
    the same silent miss, in the opposite direction.
    """
    page = _open_full_bill(chromium, tmp_path)
    # Dropping the trailing hyphen leaves "…General Serv", which is a prefix of
    # both the split occurrence on rows 1-2 and the whole "Services" on row 3,
    # so two matches is correct here — the point is that it is no longer zero.
    assert _find(page, "Administrator of General Serv-") == "1 / 2"
    # The first hit is the line the reader copied from.
    row = page.evaluate(
        """() => document.querySelector('mark.find-hit--current')
                        .closest('.full-text-line').querySelector('.full-text-line__number').textContent.trim()"""
    )
    assert row == "1"
    page.close()


def test_csp_blocks_object_element(live_url, chromium):
    """CSP object-src 'none' blocks <object> elements (#282).

    The CSP header includes object-src 'none'. This test verifies the browser
    actually enforces it by injecting an <object> element pointing to a valid
    resource and confirming it fails to load.

    If object-src is removed or weakened to 'self', the object would load and
    the test would fail. This proves the defense-in-depth layer is active, not
    just the header string being present.
    """
    page = chromium.new_page()
    # Navigate to the sample report which carries the CSP header
    page.goto(f"{live_url}/sample/example.html", wait_until="domcontentloaded")

    # Inject an <object> element pointing to a simple HTML resource on the same origin.
    # object-src 'none' should prevent it from loading.
    # We use a longer timeout and check for CSP violation in console.
    csp_violation = page.evaluate(
        """() => new Promise((resolve) => {
            const violations = [];
            const originalError = console.error;
            console.error = (...args) => {
                if (args[0] && args[0].includes && args[0].includes('Content Security Policy')) {
                    violations.push(args[0]);
                }
                originalError.apply(console, args);
            };

            const obj = document.createElement('object');
            obj.type = 'text/html';
            obj.data = '/index.html';  // Same-origin, would load without CSP
            obj.style.width = '100px';
            obj.style.height = '100px';

            let loaded = false;
            obj.onload = () => { loaded = true; };

            document.body.appendChild(obj);

            // Wait for load or CSP violation
            setTimeout(() => {
                console.error = originalError;
                // If loaded is true, the object loaded (CSP not blocking).
                // If violations array has CSP errors, CSP blocked it.
                // If neither, assume CSP blocked (some browsers don't fire onload/onerror).
                resolve({ loaded, violations });
            }, 2000);
        })"""
    )

    assert csp_violation["loaded"] is False, (
        "<object> element loaded (onload fired) despite object-src 'none' — CSP enforcement is not working"
    )

    # If we got CSP violations, that's proof the browser enforced it
    # If not, the browser may have silently blocked without firing events
    # Both cases indicate CSP is working (object didn't load)
    page.close()


def test_csp_base_uri_none_ignores_base_tag(live_url, chromium):
    """CSP base-uri 'none' causes the browser to ignore <base> tags (#282).

    The CSP header includes base-uri 'none'. This test verifies the browser
    actually enforces it by injecting a <base> tag with a different path and
    confirming relative URLs resolve against the document's origin, not the base.

    If base-uri is removed or weakened to 'self', the <base> tag would be honored
    and relative URLs would resolve against the base path. This proves the
    defense-in-depth layer is active.
    """
    page = chromium.new_page()
    page.goto(f"{live_url}/sample/example.html", wait_until="domcontentloaded")

    # Test with base-uri 'none': inject a <base> tag and try to load a relative resource
    # With base-uri 'none', the browser should ignore the <base> tag and resolve
    # relative URLs against the document's origin.
    result_with_csp = page.evaluate(
        """() => new Promise((resolve) => {
            // Add a base tag pointing to a different path
            const base = document.createElement('base');
            base.href = '/different/path/';
            document.head.appendChild(base);

            // Now try to load a relative resource (no leading slash)
            const img = document.createElement('img');
            img.src = 'test.jpg';  // Relative URL
            img.style.display = 'none';

            let loaded = false;
            let errored = false;
            img.onload = () => { loaded = true; };
            img.onerror = () => { errored = true; };

            document.body.appendChild(img);

            setTimeout(() => {
                // With base-uri 'none', currentSrc should be relative to document origin
                // (i.e., /sample/test.jpg), not the base path (/different/path/test.jpg)
                resolve({
                    loaded,
                    errored,
                    currentSrc: img.currentSrc,
                    ignoredBase: img.currentSrc.includes('/sample/')
                });
            }, 3000);
        })"""
    )

    assert result_with_csp["ignoredBase"] is True, (
        f"<base> tag was honored despite base-uri 'none' — "
        f"CSP enforcement is not working. currentSrc: {result_with_csp['currentSrc']}"
    )
    page.close()
