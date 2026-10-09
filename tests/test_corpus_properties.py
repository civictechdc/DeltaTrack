"""Property-based tests that run against the full bill XML corpus.

These are diagnostic tests, not TDD-driven. They check invariants that should
hold across all bill versions and surface issues mechanically. Failures here
indicate parser gaps, not test bugs.
"""

import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

from deltatrack.bill_tree import (
    Division,
    _extract_appropriations_text,
    _extract_section_text,
    amount_text,
    extract_text_content,
    find_bill_body,
    walk_body_sections,
)
from tests.conftest import assert_manifest_committed, manifest_xml_files, manifest_xml_ids
from tests.parsed_bills import parsed_bill

pytestmark = pytest.mark.slow

# The gate parametrizes over the COMMITTED corpus manifest (tests/corpus_manifest.toml),
# not a `bills/*` glob, so the collected set is identical on every machine and in CI
# (fail closed if a fixture is uncommitted) rather than whatever a machine fetched
# (fail open on an empty glob). CORPUS_SWEEP=1 swaps in the broad local glob for
# opt-in, non-CI exploration. See docs/decisions/0015-corpus-test-fixtures.md.
ALL_XML_FILES = manifest_xml_files()
DOLLAR_RE = re.compile(r"\$[\d,]+")

# Tags whose subtrees should be excluded from raw text collection.
# <quote> contains cited text, not appropriations content.
# <header> text is stored in header_text, not body_text.
_SKIP_TAGS = {"quote", "header"}

# Amounts that reach no node, each traced to a filed defect rather than absorbed into a
# tolerance. Keyed by fixture id, then by the amount's SOURCE spelling.
#
# The point of naming them individually is that the gate stays exact for everything else:
# a new hole cannot hide behind slack left for an old one, which is what a ratio allows.
# test_every_dollar_amount_appears_in_a_node also fails if an entry here stops being
# missing, so a fix removes its entry rather than leaving dead tolerance behind.
# Empty, and that is its intended resting state rather than a gap waiting to be filled:
# every dollar amount in every committed bill reaches a node, so the assertion is exact
# with nothing carved out of it.
#
# It held three entries while this branch was open, all three the same defect (#465, a
# section sitting directly under a division was walked by nothing): the LIBOR findings
# figure and "the sum of $700" in 117-hr-2471, and "$35 per coin for the $5 coin" in
# 116-hr-1865. Fixing #465 made all three reachable, and the stale-entry assertion below
# is what required their removal here rather than leaving three dead exemptions behind.
# That is the mechanism doing its job once, in the situation it exists for.
KNOWN_UNCOVERED_AMOUNTS: dict[str, dict[str, str]] = {}


def _collect_body_text_excluding(body: ET.Element, skip_tags: set[str]) -> str:
    """Walk the element tree, collecting text but skipping subtrees with tags in skip_tags."""
    parts: list[str] = []

    def _walk(el: ET.Element) -> None:
        if el.tag in skip_tags:
            return
        if el.text:
            parts.append(el.text)
        for child in el:
            _walk(child)
            if child.tail:
                parts.append(child.tail)

    _walk(body)
    return " ".join(parts)


def _extract_dollar_matches(text: str) -> list[tuple[int, str]]:
    """Find all non-zero dollar amounts as ``(value, literal)`` pairs.

    The literal is the source spelling. Callers that ask "did this amount reach a
    node" must search for the literal rather than for a re-formatted ``f"${value:,}"``,
    because the round trip through ``int`` is lossy on malformed source: 118-s-4797
    carries ``$60,00,000``, which reformats to ``$6,000,000`` and is then findable in
    no node, though the section holding it is present and intact. Comparing literals
    removes that class of false positive; measured across the committed corpus it
    drops the reported misses from 2 to 1 and introduces none.
    """
    matches = []
    for m in DOLLAR_RE.finditer(text):
        value = int(m.group().replace("$", "").replace(",", ""))
        if value > 0:
            matches.append((value, m.group()))
    return matches


def _xml_id(xml_path: Path) -> str:
    """Create a readable test ID from a bill XML path."""
    return f"{xml_path.parent.name}/{xml_path.name}"


def _skip_if_absent(xml_path: Path) -> None:
    """Skip (not error) a manifest case whose fixture is absent from a partial local
    checkout, keeping collected = passed + skipped constant. The completeness floor
    (test_manifest_fixtures_committed) turns any such absence red in CI."""
    if not xml_path.exists():
        pytest.skip(f"manifest fixture not present locally: {_xml_id(xml_path)}")


def test_manifest_fixtures_committed() -> None:
    """Fail-closed completeness floor for the corpus property gates (#217, ADR 0015).

    These gates parametrize over the committed manifest (ALL_XML_FILES). This guard —
    which always runs, no env var — fails (not skips) if any manifested fixture is
    absent from the checkout, so a fresh CI checkout missing a committed bill goes red
    instead of silently collecting fewer cases. Under CORPUS_SWEEP it still validates
    that the committed manifest subset is present (the local glob is a superset).
    """
    assert_manifest_committed(ALL_XML_FILES, "corpus-properties")


def test_known_uncovered_amounts_names_live_fixtures() -> None:
    """Every ``KNOWN_UNCOVERED_AMOUNTS`` key is a fixture that still exists.

    The allowlist is meant to be self-cleaning: ``test_every_dollar_amount_appears_in_a_node``
    fails when an entry stops being missing, so a fixed defect forces its entry out. That
    guarantee has one gap, and it is silent. Entries are read with
    ``KNOWN_UNCOVERED_AMOUNTS.get(test_id, {})``, so a key naming a fixture that has since
    been renamed or retired is consulted by no test case at all: it can never be reported
    missing, and can never be reported stale either. It just sits there, and the next
    reader takes it for a live exemption.

    A typo made when the entry is written is already caught, because the real fixture then
    fails with the amount unexplained. This covers the other direction, where the entry was
    correct and the corpus moved underneath it, which has happened before (#10 renamed
    corpus files).
    """
    orphans = sorted(set(KNOWN_UNCOVERED_AMOUNTS) - {_xml_id(path) for path in ALL_XML_FILES})
    assert not orphans, (
        f"KNOWN_UNCOVERED_AMOUNTS names {len(orphans)} fixture(s) not in the manifest: {orphans}. "
        f"Re-point each entry at the fixture's current id, or drop it if the bill is gone."
    )


def test_known_duplicate_counts_names_manifest_fixtures() -> None:
    """Every ``_KNOWN_DUPLICATE_COUNTS`` key is a MANIFESTED fixture (#496).

    The dict holds per-file ceilings, so a key nothing evaluates is a number nothing can
    keep current — and it fails in the worst direction, because the parser reaching further
    raises the true count until the entry starts failing whoever does run it. Four keys had
    already drifted that way, unnoticed for as long as they existed, since the only people
    who saw them were running CORPUS_SWEEP=1 while investigating something else.

    Deliberately keyed on ``manifest_xml_ids()`` rather than the collected
    ``ALL_XML_FILES``: that list widens to the local `bills/` tree under CORPUS_SWEEP=1, so
    on a fetched machine a sweep-only key would resolve and this guard would pass — the
    same fail-open it exists to close, and the reason the sibling guard above cannot be
    reused as-is. Under the sweep this must still check the manifest, and it does.

    A stale VALUE is a separate problem this cannot see: the assertion is `<=`, so a parser
    change that reduces collisions leaves a ceiling silently loose (#474 did exactly that).
    """
    orphans = sorted(set(_KNOWN_DUPLICATE_COUNTS) - manifest_xml_ids())
    assert not orphans, (
        f"_KNOWN_DUPLICATE_COUNTS names {len(orphans)} fixture(s) not in the corpus manifest: "
        f"{orphans}. Only CORPUS_SWEEP=1 could reach them and nothing keeps their numbers "
        f"current, so commit and manifest the bill (#126) or drop the entry — a sweep-only "
        f"file is reported rather than asserted by test_no_duplicate_match_paths."
    )


@pytest.mark.parametrize(
    "xml_path",
    ALL_XML_FILES,
    ids=[_xml_id(p) for p in ALL_XML_FILES],
)
def test_every_dollar_amount_appears_in_a_node(xml_path: Path) -> None:
    """Every dollar amount in the raw XML body appears in at least one node's body_text.

    Excludes amounts inside <quote> and <header> elements (stored separately).

    The cap is absolute, not a ratio, because a ratio cannot express this property on
    documents of this size. A percentage tolerance scales with the bill: at the 0.98 it
    replaces, 117-hr-2471 could lose 67 of its 3,385 amounts and stay green, and the
    slack was widest on the largest bills, which is where the money is. It also cannot
    see loss that is large in sections but small in dollars -- 13 divisions of that bill
    reach no node at all (#465) and it still scored 0.9997, because the missing divisions
    are policy text carrying one dollar amount between them.

    Anything genuinely uncovered belongs in ``KNOWN_UNCOVERED_AMOUNTS`` against a filed
    defect, not inside a tolerance that also silently absorbs the next regression.
    """
    _skip_if_absent(xml_path)
    test_id = _xml_id(xml_path)
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Every body, selected here rather than through the parser's own selector, so a body
    # the parser skips is still counted: a reported bill's two texts (#434), an engrossed
    # amendment's several blocks (#836).
    try:
        bodies = (
            root.findall("legis-body")
            or root.findall(".//engrossed-amendment-body/amendment/amendment-block")
            or [find_bill_body(root)]
        )
    except ValueError:
        pytest.skip("No bill body found")

    # Collect dollar amounts from raw XML, excluding quote/header subtrees
    raw_text = " ".join(_collect_body_text_excluding(body, _SKIP_TAGS) for body in bodies)
    raw_matches = _extract_dollar_matches(raw_text)

    if not raw_matches:
        pytest.skip("No dollar amounts in bill body")

    if len(raw_matches) < 3:
        # Shell bills (procedural placeholders later replaced with full text)
        # have 1-2 amounts. Missing 1 of 1 gives 0% coverage, which is noise.
        pytest.skip(f"Shell bill: only {len(raw_matches)} amounts, too few for meaningful coverage")

    # Parse with the actual parser
    bill_tree = parsed_bill(xml_path)
    all_body_text = " ".join(node.body_text for node in bill_tree.nodes)

    # Search for the source spelling, not a re-formatted f"${value:,}" -- see
    # _extract_dollar_matches for why the round trip invents misses on malformed source.
    #
    # The trailing lookahead is what makes this a search for the AMOUNT rather than for
    # its digits. A plain containment test finds "$35" inside "$356,000", so a dropped
    # small amount reads as present whenever some larger amount happens to start with the
    # same digits, and the check then passes for a reason that has nothing to do with the
    # amount it was asked about. Two live instances on the committed corpus, both real
    # amounts reaching no node while this gate called them found: "$35 per coin for the
    # $5 coin" (116-hr-1865) and "the sum of $700" (117-hr-2471), each masked by a longer
    # amount elsewhere in the same bill.
    missing = sorted(
        {
            literal
            for _value, literal in raw_matches
            if not re.search(re.escape(literal) + r"(?![\d,]*\d)", all_body_text)
        }
    )
    allowed = KNOWN_UNCOVERED_AMOUNTS.get(test_id, {})

    unexpected = [literal for literal in missing if literal not in allowed]
    assert not unexpected, (
        f"{test_id}: {len(unexpected)} of {len(raw_matches)} amounts appear in no node: {unexpected[:5]}. "
        f"If this is a known defect, file it and add it to KNOWN_UNCOVERED_AMOUNTS with the issue."
    )

    # The allowlist is self-cleaning: an entry that stops being missing is a fixed defect,
    # and leaving it behind would let the gate keep tolerating a hole that has closed.
    # Without this, the allowlist decays into exactly the open-ended tolerance the ratio
    # was, one entry at a time.
    stale = [literal for literal in allowed if literal not in missing]
    assert not stale, (
        f"{test_id}: {stale} now reach a node, so their KNOWN_UNCOVERED_AMOUNTS entries "
        f"are obsolete. Remove them (and close the issue they name if nothing else blocks it)."
    )


def test_no_section_sibling_is_dropped_from_every_node() -> None:
    """A section with appropriations children keeps its other children too (#459).

    Scoped to the shape that produced the loss: when a section carries ``appropriations-*``
    children (the elements holding an account and its amount), its other children -- a
    <list>, a <continuation-text>, a <quoted-block> -- must still reach some node.

    They used to reach none, because that branch built the section's node from the opening
    <text> alone. The money vanished from both renderings, which is why no comparison
    between two views could detect it, and why the loss had to be measured against the
    source XML instead.

    Swept across the whole corpus in ONE case rather than parametrized per fixture, and
    that is deliberate. Most fixtures contain no section of this shape, so a per-fixture
    gate would content-skip the large majority of cases, and every one of those skips
    would have to be declared in ALLOWED_CORPUS_SKIPS (#220) to say nothing at all. A
    single sweep carries its own fail-closed floor instead: it asserts that the corpus
    actually presented instances to check, so "nothing dropped" can never mean "nothing
    looked at".

    Amounts are matched the same way ``test_every_dollar_amount_appears_in_a_node`` matches
    them: on the SOURCE literal, with a trailing boundary so the search is for the amount
    rather than for its digits. The two failure modes that motivated it there apply here
    unchanged -- a plain containment test finds "$35" inside "$356,000", so a dropped
    sibling can read as present, and re-formatting through ``int`` invents misses on
    malformed source such as ``$60,00,000``. Measured across the committed corpus this
    changes no current result (both forms report zero drops); it removes two ways for this
    gate to be wrong later, rather than fixing something visible today.
    """
    checked = 0
    dropped = []
    for xml_path in ALL_XML_FILES:
        if not xml_path.exists():
            continue
        root = ET.parse(xml_path).getroot()
        try:
            find_bill_body(root)
        except ValueError:
            continue

        sections = [s for s in root.iter("section") if any(c.tag.startswith("appropriations-") for c in s)]
        if not sections:
            continue

        tree = parsed_bill(xml_path)
        all_text = " ".join(f"{n.body_text} {n.display_text}" for n in tree.nodes)

        for section in sections:
            for child in section:
                if child.tag in ("enum", "header", "text") or child.tag.startswith("appropriations-"):
                    continue
                matches = _extract_dollar_matches(extract_text_content(child))
                if not matches:
                    continue
                checked += 1
                missing = [
                    literal
                    for _value, literal in matches
                    if not re.search(re.escape(literal) + r"(?![\d,]*\d)", all_text)
                ]
                if missing:
                    enum = section.find("enum")
                    label = (enum.text or "").strip() if enum is not None else "?"
                    dropped.append(f"{_xml_id(xml_path)} sec.{label} <{child.tag}> {missing[:3]}")

    # The floor. 19 money-bearing siblings exist across the committed fixtures, 8 of which
    # were dropped before #459. Requiring most of them keeps the gate honest if a fixture
    # is retired, while still going red if the corpus stops exercising this shape at all.
    assert checked >= 15, (
        f"only {checked} money-bearing siblings found corpus-wide; this gate is not exercising anything"
    )
    assert dropped == [], f"{len(dropped)} of {checked} money-bearing siblings appear in no node: {dropped[:3]}"


def test_no_top_level_legis_body_is_silently_discarded() -> None:
    """Every top-level <legis-body> contributes to the tree (#434).

    A reported bill carrying a committee substitute prints two complete texts as two
    sibling <legis-body> elements. ``find_bill_body`` returned the first and nothing
    looked for the rest, so the second text reached no node, no full-bill view and no
    money diff -- 459 documents in the local collection, 3,736 sections, silently.

    Asserted as text reaching the tree rather than as a body COUNT, because a count is
    satisfied by walking a body and dropping everything in it. The check is that a
    distinctive amount from each body is present, which is the property a reader depends
    on and the one that broke.

    This is the invariant that survives whichever direction #186 takes for representing
    the duality: whether the second text ends up marked, sectioned off or diffed
    separately, no body may contribute nothing. It deliberately does NOT assert which
    body is authoritative -- a corpus audit found four different shapes here (base plus
    substitute; two competing committee substitutes from sequential referral; an empty
    body paired with a real one; and complementary halves of one bill), and no attribute
    picks the right single body across them.

    Carries a fail-closed floor for the same reason the sweep above does: "nothing
    discarded" must never be able to mean "no multi-body document was looked at".
    """
    multi_body_files = 0
    missing: list[str] = []
    for xml_path in ALL_XML_FILES:
        if not xml_path.exists():
            continue
        root = ET.parse(xml_path).getroot()
        bodies = root.findall("legis-body")
        if len(bodies) < 2:
            continue
        multi_body_files += 1

        tree_text = " ".join(amount_text(node) for node in parsed_bill(xml_path).nodes)
        for index, body in enumerate(bodies):
            # Amounts unique to THIS body. An amount also present in a sibling body
            # cannot show that this body was walked, since the sibling would supply it.
            own = set(DOLLAR_RE.findall(extract_text_content(body)))
            others = set()
            for other_index, other in enumerate(bodies):
                if other_index != index:
                    others |= set(DOLLAR_RE.findall(extract_text_content(other)))
            distinctive = own - others
            if not distinctive:
                continue
            absent = [amount for amount in sorted(distinctive) if amount not in tree_text]
            if absent:
                missing.append(f"{xml_path.parent.name}/{xml_path.name} body[{index}]: {absent[:3]}")

    # The floor. 114-hr-2029/4 is the committed two-body fixture; it was withheld from
    # the corpus while #434 was open precisely because committing it reddened the suite.
    assert multi_body_files >= 1, (
        "no multi-<legis-body> document in the committed corpus; this gate is not exercising anything"
    )
    assert missing == [], f"{len(missing)} of {multi_body_files} multi-body documents lose a body's text: {missing[:3]}"


# Files known to have duplicate match_paths (cross-division collisions, issue #1).
# Values are EXACT, asserted as `total_dupes == known`. Files not listed must have zero
# duplicates, so an entry that reaches zero is deleted rather than set to 0.
#
# EVERY KEY NAMES A MANIFESTED FIXTURE, enforced by
# test_known_duplicate_counts_names_manifest_fixtures. That is the #496 fix. Until then the
# dict also carried keys for bills present only in a local `bills/` tree, reachable solely
# under CORPUS_SWEEP=1 — so no CI run and no clean checkout ever evaluated them, nothing
# could hold them current, and four had drifted below the true count, failing the sweep for
# anyone who turned it on. Six were dropped rather than recalibrated (#496): a number no run
# checks goes stale again, and half of them were unreachable even under the sweep, because
# sweep_bill_dirs yields ONE directory per bill id with the committed copy winning, so a
# download-only VERSION of a partly-committed bill is shadowed. A sweep-only file is now
# reported, not asserted — see test_no_duplicate_match_paths.
#
# They were CEILINGS (`<= known`) until #513, which only fail upward: a parser change that
# REDUCED collisions left the stored number above the truth with nothing red to say so, and
# that dead slack then licensed exactly as many future regressions in silence. #474 (joining
# an account split across two source elements) did it to 19 of these 30 keys at once, 272
# duplicates of slack in total, days after #482 had measured every one of them correct.
# Equality is the fix and it has a cost: a genuine parser improvement now reddens the suite
# until the number is lowered here. That is the forcing function, not a nuisance — every
# value below was re-measured when equality landed, and the assertion is what keeps them so.
_KNOWN_DUPLICATE_COUNTS: dict[str, int] = {
    # #465 note: a division's bare <section> children (a short-title/definitions preamble
    # ahead of TITLE I, or a whole policy division organised without titles) were reached
    # by nothing and entered no node. Walking them adds real sections whose match_paths
    # are division-stripped, so "sec. 1" recurs once per division that has one. Every
    # increase below was verified to be exactly that class, and cross-version pairing was
    # checked directly: of the 25 such entries that pair across adjacent committed
    # versions, 24 pair within the same division and the one that does not is a bill that
    # gained divisions between versions. Collision-group matching (#1) resolves them by
    # division_label, which these nodes now carry.
    # #188 note: subsection nodes inherit their section's match_path as a prefix, so
    # a cross-division duplicated section duplicates its subsection paths one level
    # deeper. Every #188 count increase was verified to be exactly that class (new
    # duplicate keys are all subsection nodes under already-colliding sections) —
    # the same collision-group matching (#1) covers them.
    "113-hr-3547/5_engrossed-amendment-house.xml": 144,
    # Enrolled has 12 divisions whose later titles spill out as orphan <title>
    # siblings. Walking them (#146) surfaces genuine cross-division collisions
    # (general provisions, same-named bureaus across divisions) on division-stripped
    # match_paths — now matching the engrossed-amendment version's count (was 73 when
    # the orphan titles were silently dropped). Real source structure, not a bug.
    "113-hr-3547/6_enrolled-bill.xml": 144,
    "113-hr-83/6_engrossed-amendment-house.xml": 107,
    "113-hr-83/7_enrolled-bill.xml": 107,
    # #434 note: this version is a reported bill carrying a committee substitute, so
    # the document prints TWO complete texts as sibling <legis-body> elements and both
    # are now walked. The collisions are the two texts restating the same accounts
    # ("military construction, army" appears in each), not a parser fault: they are what
    # the source contains, and they would vanish only by dropping one of the texts
    # again. Collision-group matching separates them by text similarity, NOT by which
    # body they came from -- pairing on the body position is a false cross-version
    # match, see diff_bill._match_collision_group.
    "114-hr-2029/4_reported-in-senate.xml": 119,
    # #836: the House amendment's second amendment block (Division Q, the PATH Act) is
    # now read as a second body. Its sections restate Sec. 1, 2, ... as every division
    # does, which brings this version to the enrolled bill's count: same text, same 154.
    "114-hr-2029/6_engrossed-amendment-house.xml": 154,
    "114-hr-2029/7_enrolled-bill.xml": 154,
    "115-hr-1625/6_enrolled-bill.xml": 171,
    "115-hr-244/6_enrolled-bill.xml": 147,
    "115-hr-5895/2_engrossed-in-house.xml": 22,
    "115-hr-5895/4_engrossed-amendment-senate.xml": 7,
    # Enrolled version places Division C's TITLE II-V at <legis-body> level beside the
    # divisions (not nested). Walking them (#146) surfaces genuine cross-division
    # collisions: the orphaned "TITLE V—General provisions" (sec. 501-505) shares a
    # division-stripped match_path with Division A's "TITLE V—General provisions".
    # Real source structure, not a parser error (cf. 119-hr-1's twin Sec. 10012).
    "115-hr-5895/5_enrolled-bill.xml": 7,
    "116-hr-1865/6_enrolled-bill.xml": 44,
    "118-hr-2882/5_engrossed-amendment-house.xml": 55,
    "118-hr-4366/4_engrossed-amendment-senate.xml": 7,
    "118-hr-4366/5_engrossed-amendment-house.xml": 30,
    "118-hr-4366/6_enrolled-bill.xml": 30,
    # Fresh bills added for overfitting smoke test (2026-04-15)
    "117-hr-4432/1_reported-in-house.xml": 1,
    "117-hr-4502/1_reported-in-house.xml": 1,
    "117-hr-4502/2_engrossed-in-house.xml": 33,
    "118-hr-4820/1_reported-in-house.xml": 5,
    # Fresh bill added for Part C smoke test (2026-04-15). Its 116-hr-133 companions were
    # the only sweep-only keys that a sweep could actually reach, and both were stale (160
    # against a measured 206); dropped in #496 rather than repinned, since committing that
    # bill is #126's call and 206 is not a number CI can hold.
    "117-hr-2471/6_enrolled-bill.xml": 210,
    # Committee-report external-validation bills (#8/#44). All duplicates are benign
    # cross-section heading collisions (a heading repeated across the appropriation, a
    # limitation/administrative-provisions section, and general provisions), not parser
    # errors. These Senate prints are committed (tests/corpus/118-s-*) and named in
    # the corpus manifest, so the gate runs them in CI; these counts are its baselines.
    # 118-s-4927 (Energy-Water) is deliberately absent: it held 4 and now produces none, so
    # it asserts zero as an unlisted file. Equality (#513) is what surfaced that; under the
    # old ceiling it would have kept tolerating 4 collisions the parser no longer makes.
    "118-s-4795/1_reported-in-senate.xml": 2,  # CJS: DOJ general-provisions + NASA pair
    "118-s-4796/1_reported-in-senate.xml": 5,  # Transportation-HUD: FAA/FHWA/NHTSA/HUD repeats
    "118-s-4797/1_reported-in-senate.xml": 1,  # State-Foreign Ops: callable-capital limitation
    "118-s-4802/1_reported-in-senate.xml": 3,  # Interior-Environment: Forest Service repeats
    "118-s-4928/1_reported-in-senate.xml": 5,  # Financial Services: Treasury/OPM salaries, DC funds
    "118-s-4942/1_reported-in-senate.xml": 2,  # Labor-HHS: VETS employment-and-training lines
    "118-s-2321/1_reported-in-senate.xml": 1,  # CJS FY2024 (out-of-corpus guard): NASA pair
    # 119-hr-1 (reconciliation): two genuinely-distinct Sec. 10012 in the reported version
    # (Alien SNAP eligibility + Emergency food assistance), one renumbered to 10013 later.
    # Real source duplicate, not a parser error; exposes the matcher's reliance on body
    # similarity over header (tracked in DeltaTrack#8). Committed + in the manifest, so CI runs it.
    "119-hr-1/1_reported-in-house.xml": 1,
    # 118-hr-9468: two enum-less body-level sections — the enacting "the following sums
    # are appropriated" lead-in and the closing short-title section — both address to the
    # empty tuple, because walk_body_sections derives a section's path from its <enum> and
    # these have none. A parser limitation rather than a source duplicate, and independent
    # of appropriations: neither node is an account.
    #
    # Recorded rather than fixed here because it is a different defect from #485 (the
    # accounts under such a section reaching no node at all) and wants its own change:
    # giving an enum-less section an address means choosing one, which is a design call
    # this fix does not need to make. #485's fix REDUCED this count from 2 to 1 by giving
    # the appropriations section's content to named account nodes. The gate asserts a
    # ceiling, not equality, so a later fix tightens this without a test edit.
    "118-hr-9468/1_introduced-in-house.xml": 1,
    "118-hr-9468/4_enrolled-bill.xml": 1,
}


def _assert_duplicate_baseline(test_id: str, total_dupes: int, known: int, dupes: dict[str, int]) -> None:
    """Compare a measured duplicate count against its stored baseline, EXACTLY.

    Extracted from the gate below so the downward branch can be exercised directly:
    every committed fixture sits at ``total_dupes == known``, so a corpus run never
    reaches the staleness case, and an assertion that has never once fired is
    indistinguishable from one that cannot. ``test_duplicate_baseline_compares_both
    _directions`` supplies the cases the corpus does not.
    """
    # EXACT, not a ceiling (#513). `<= known` only fails upward, so a parser change that
    # REDUCES collisions leaves the stored number above the truth with nothing red to say
    # so — and that dead slack then licenses exactly as many future regressions silently.
    # #474 (joining an account split across two source elements) did this to 19 of 30 keys
    # at once, 272 duplicates of slack in total, days after #482 had measured them correct.
    # Equality costs churn: a genuine parser improvement now reddens the suite until the
    # number is lowered. That is the point, and it is the same forcing function the repo
    # relies on elsewhere — a baseline is only worth having if a build makes it recalibrate.
    if total_dupes > known:
        raise AssertionError(
            f"{test_id}: duplicate match_paths INCREASED from {known} to {total_dupes}. "
            f"A section identity regression, or real new collisions in the source. "
            f"Sample: {list(dupes.items())[:3]}"
        )
    if total_dupes < known:
        raise AssertionError(
            f"{test_id}: duplicate match_paths DECREASED from {known} to {total_dupes}, so the "
            f"stored baseline is stale and now tolerates {known - total_dupes} future regression(s) "
            f"in silence. Lower the entry to {total_dupes}"
            f"{' and drop it, since the file now has none' if total_dupes == 0 else ''}."
        )


def test_duplicate_baseline_compares_both_directions() -> None:
    """The baseline comparison goes RED above AND below the stored number.

    The downward (stale-baseline) branch is the whole point of #513, and no committed
    fixture can reach it: recalibrating the baselines put every one of them at equality,
    which is exactly the state in which a silent reversion to `<= known` would leave the
    suite green. These are the cases the corpus cannot supply.
    """
    dupes = {"/div-a/sec-1": 2}

    # At the baseline: passes.
    _assert_duplicate_baseline("t", 3, 3, dupes)

    # Above it: the regression the ceiling already caught.
    with pytest.raises(AssertionError, match="INCREASED from 3 to 4"):
        _assert_duplicate_baseline("t", 4, 3, dupes)

    # Below it: the staleness #513 exists to catch.
    with pytest.raises(AssertionError, match="DECREASED from 3 to 1"):
        _assert_duplicate_baseline("t", 1, 3, dupes)

    # A baseline that has fallen to zero says to remove the entry, not lower it to 0.
    with pytest.raises(AssertionError, match="drop it, since the file now has none"):
        _assert_duplicate_baseline("t", 0, 3, dupes)


@pytest.mark.parametrize(
    "xml_path",
    ALL_XML_FILES,
    ids=[_xml_id(p) for p in ALL_XML_FILES],
)
def test_no_duplicate_match_paths(xml_path: Path) -> None:
    """Each node's match_path should be unique within a bill.

    Duplicates indicate cross-division path collisions (issue #1).
    Files with known duplicates assert the count matches the stored baseline exactly,
    in both directions (#513). Files with no known duplicates assert zero.

    A file reachable only under CORPUS_SWEEP=1 is REPORTED, not asserted (#496). The
    baselines here are calibrated against the committed corpus; the sweep is an
    uncalibrated superset, so there is no trustworthy number to compare a sweep-only file
    against — asserting zero would fail every real bill that has collisions, and pinning a
    measured count would pin a number no run can keep current, which is how four of these
    drifted into failing the sweep for anyone who turned it on. The same reasoning already
    exempts the PDF-layout registry in test_corpus_tree_properties. The file is still
    parsed, so a crash or an empty tree still surfaces here; only the comparison is
    withheld, and -rs prints the count so exploration still sees it.
    """
    _skip_if_absent(xml_path)
    test_id = _xml_id(xml_path)
    bill_tree = parsed_bill(xml_path)

    if not bill_tree.nodes:
        pytest.skip("No nodes parsed")

    counts = Counter(node.match_path for node in bill_tree.nodes)
    dupes = {k: v for k, v in counts.items() if v > 1}
    total_dupes = sum(v - 1 for v in dupes.values())

    if test_id not in manifest_xml_ids():
        pytest.skip(f"Sweep-only file, no calibrated baseline: {total_dupes} duplicate match_paths (#496)")

    known = _KNOWN_DUPLICATE_COUNTS.get(test_id, 0)
    _assert_duplicate_baseline(test_id, total_dupes, known, dupes)


_APPRO_TAGS = {"appropriations-major", "appropriations-intermediate", "appropriations-small"}

# Files whose appropriations elements the parser does not reach, with the count it
# misses. EMPTY: every file now reaches all of them, so the gate asserts zero missing
# everywhere and any regression trips the assertion rather than being absorbed by a
# stored count. Add an entry only with a comment saying which elements are unreachable
# and why; a baseline above the true count silently permits that many regressions.
#
# History: #146, #482 — three baselines outlived the gaps they recorded. 113-hr-3547
# enrolled held 310 against a true count of 0 (it was the pre-#146 figure, from before
# normalize_bill walked top-level titles); the two 116-hr-133 entries held 1 against 0.
# 115-hr-5895 enrolled was the same shape, dropped in #479. Measured entry by entry in
# #482 — the 116-hr-133 pair only under CORPUS_SWEEP=1, since neither is committed.
_KNOWN_MISSING_APPRO: dict[str, int] = {}


def _normalize_ws(text: str) -> str:
    """Collapse whitespace for comparison."""
    return " ".join(text.split())


@pytest.mark.parametrize(
    "xml_path",
    ALL_XML_FILES,
    ids=[_xml_id(p) for p in ALL_XML_FILES],
)
def test_every_appropriations_element_with_text_produces_node(xml_path: Path) -> None:
    """Every appropriations-* element with text content should map to a parsed node.

    Extracts text using the same function the parser uses, then checks that
    the normalized text appears in at least one node's body_text.
    """
    _skip_if_absent(xml_path)
    test_id = _xml_id(xml_path)

    tree = ET.parse(xml_path)
    root = tree.getroot()

    try:
        body = find_bill_body(root)
    except ValueError:
        pytest.skip("No bill body found")

    # Find all appropriations elements with text content
    appro_elements = []
    for el in body.iter():
        if el.tag in _APPRO_TAGS:
            text = _extract_appropriations_text(el)
            if text.strip():
                appro_elements.append((el, text))

    if not appro_elements:
        pytest.skip("No appropriations elements with text")

    # Parse and collect all node body texts (normalized)
    bill_tree = parsed_bill(xml_path)
    node_texts = [_normalize_ws(node.body_text) for node in bill_tree.nodes]

    # Check each appropriations element's text appears in some node
    missing = []
    for el, text in appro_elements:
        normalized = _normalize_ws(text)
        if not any(normalized in nt for nt in node_texts):
            preview = normalized[:80]
            missing.append((el.tag, el.attrib.get("id", "?"), preview))

    total = len(appro_elements)
    total - len(missing)

    known_missing = _KNOWN_MISSING_APPRO.get(test_id, 0)

    if known_missing == 0:
        assert len(missing) == 0, (
            f"{test_id}: {len(missing)}/{total} appropriations elements not found in nodes. Sample: {missing[:3]}"
        )
    else:
        assert len(missing) <= known_missing, (
            f"{test_id}: missing count increased from {known_missing} to {len(missing)}. Sample: {missing[:3]}"
        )


# Ancestor tags that mark AMENDMENT PAYLOAD: text the document proposes to insert
# somewhere else, rather than text this bill enacts. <quoted-block> holds the block an
# amendment inserts; the <amendment-doc>/<amendment-block>/<amendment> family is the
# amendment wrapper itself. The parser does not node-ize either, so their <section>
# descendants are outside the coverage property below (#11 tracks the amendment-doc gap).
#
# This is deliberately expressed as a set of SOURCE STRUCTURE tags, not as anything
# derived from which sections the parser currently misses. An exclusion phrased as "the
# sections we know we drop" restates the defect as the specification, and the gate can
# then never fail: every new drop looks like a member of its own exemption. These tags
# come from the GPO bill DTD and would mean the same thing if the parser were rewritten.
_PAYLOAD_ANCESTOR_TAGS = frozenset({"quoted-block"})
_PAYLOAD_ANCESTOR_PREFIX = "amendment"

# Floor for the corpus-wide sweep below. The committed corpus presents 12,555 in-scope
# sections; this is a floor rather than a pinned count so retiring a fixture does not
# break it, and it sits far enough below to leave room for curation (#126) while still
# going red if section classification ever swallows the corpus wholesale.
_MIN_CORPUS_SECTIONS = 10_000


def _parent_map(root: ET.Element) -> dict[int, ET.Element]:
    """Map id(child) -> parent for every element under root.

    ElementTree elements carry no parent pointer, and the payload exclusion below is an
    ANCESTOR property, so the walk has to be reconstructed once per document.
    """
    parents: dict[int, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parents[id(child)] = parent
    return parents


def _is_amendment_payload(section: ET.Element, parents: dict[int, ET.Element]) -> bool:
    """True when any ancestor of ``section`` marks it as amendment payload."""
    current = parents.get(id(section))
    while current is not None:
        if current.tag in _PAYLOAD_ANCESTOR_TAGS or current.tag.startswith(_PAYLOAD_ANCESTOR_PREFIX):
            return True
        current = parents.get(id(current))
    return False


def _classify_sections(body: ET.Element, parents: dict[int, ET.Element]) -> tuple[list[ET.Element], int, int]:
    """Split a body's <section> elements into (in_scope, payload_count, empty_count).

    A section is EMPTY when it has no child elements and no text at all -- a bare
    ``<section/>`` placeholder. It carries nothing that could reach a node, so requiring
    it to produce one would assert on the absence of content.
    """
    in_scope: list[ET.Element] = []
    payload = 0
    empty = 0
    for section in body.iter("section"):
        if _is_amendment_payload(section, parents):
            payload += 1
        elif not len(section) and not "".join(section.itertext()).strip():
            empty += 1
        else:
            in_scope.append(section)
    return in_scope, payload, empty


def _section_reaches_a_node(section: ET.Element, node_ids: set[str]) -> bool:
    """True when ``section``'s content is represented in the tree.

    Normally that means the section's own id is a node id. A section whose every child
    is an ``appropriations-*`` account has no text of its own, so it emits no node and
    its accounts carry the content instead — the arrangement both section walkers use,
    and the point of #485: an empty placeholder node here would be exactly the unnamed,
    address-less entry that issue exists to remove, so requiring one would pin the
    defect rather than the property.

    The stand-in is allowed ONLY when the section has no body of its own, and that is
    decided with the parser's own carve and extractor rather than by looking at tags. A
    section can hold both prose and accounts:

        <section id="sec-1">
          <text>Important section-level text.</text>
          <appropriations-small id="acct-1">...</appropriations-small>
        </section>

    There the parser does emit a node for the section, carrying that prose. Accepting any
    account as a stand-in would let a regression drop the section node, and with it the
    only copy of the prose, while this gate stayed green because ``acct-1`` survived.
    Asking ``_extract_section_text`` under the appropriations carve — the exact expression
    ``walk_body_sections`` uses to decide whether to emit the node at all — makes the
    condition here the same condition as the parser's, so the two cannot drift into
    disagreeing about which sections are allowed to be absent.

    It is one account node, not all of them, because a header-only element (the naming
    half of a #474 split account) legitimately emits none. That is still enough to fail
    closed on the loss this gate was built for: a walker that drops the section drops
    its children with it, so nothing beneath it reaches a node either. What this does
    NOT check is that every account arrived — that belongs to the account-level gates in
    tests/test_bill_tree.py, which name the accounts rather than counting them.

    Rare by measurement, not by assumption: 2 of the 1,467 sections with appropriations
    children across the committed fixtures take this branch, both in 118-hr-9468, the
    corpus's only bill written without TITLE divisions.
    """
    if section.attrib.get("id", "") in node_ids:
        return True

    appro_carve = frozenset(id(c) for c in section if c.tag.startswith("appropriations-"))
    if not appro_carve:
        return False
    # Own body text of its own => the parser emits a node for it, so its absence is a
    # real loss and no account may answer for it.
    if _extract_section_text(section, appro_carve):
        return False
    return any(id(child) in appro_carve and child.attrib.get("id", "") in node_ids for child in section)


@pytest.mark.parametrize(
    "xml_path",
    ALL_XML_FILES,
    ids=[_xml_id(p) for p in ALL_XML_FILES],
)
def test_every_section_reaches_a_node(xml_path: Path) -> None:
    """Every <section> the bill enacts reaches some node, checked against the source XML.

    This replaces ``test_character_coverage_ratio`` (#9), which asserted
    ``node_chars / raw_chars >= 0.10``. That number could not be recalibrated into a
    useful gate, for two measured reasons:

    1. It is not a coverage fraction. 42 of the 43 committed fixtures score ABOVE 1.0
       (range 0.970 to 1.516 on the corpus as of this change), because the numerator
       counts text the denominator does not. A quantity that routinely exceeds 1 cannot
       be read as "the share of the bill we captured", so no threshold on it means what
       the test name claimed.
    2. It cannot see section loss. Re-injecting #465 (deleting the ``walk_body_sections``
       call for a division's bare sections) drops 151 whole sections across 7 fixtures.
       Six of those seven still score above 0.9698 -- the healthy corpus MINIMUM -- so
       any threshold loose enough to keep the corpus green passes six of the seven
       corrupted files. The loss is real and the aggregate absorbs it, because whole-bill
       character totals dilute a section that vanished.

    So the property is asserted directly and exactly instead: enumerate the sections in
    the SOURCE XML, and require each one to appear as a parsed node. Measured against the
    source rather than against another rendering of the parser's own output, because two
    views derived from the same dropped node agree with each other perfectly (#459).

    Sections are matched on the ``id`` attribute, which is the source's own identifier
    for the element and survives any renumbering the parser does to match_path.
    """
    _skip_if_absent(xml_path)
    test_id = _xml_id(xml_path)
    root = ET.parse(xml_path).getroot()

    # No skip on a missing body, deliberately. Every manifested fixture has one, so this
    # can only fire on a document that is not the shape this gate was built for -- which
    # is a finding, not a case to wave through. (Cf. #262, which turned the PDF gate's
    # zero-anchor skip into an assertion for the same reason.)
    try:
        body = find_bill_body(root)
    except ValueError as exc:
        pytest.fail(f"{test_id}: no bill body found ({exc})")

    parents = _parent_map(root)
    in_scope, payload, empty = _classify_sections(body, parents)

    if not in_scope:
        # An engrossed amendment is payload end to end: its whole body is the text it
        # proposes, so it has no enacted section to check. That is asserted rather than
        # skipped, both because the repo's content-skip ceiling (#220) would otherwise
        # need an entry declaring the #11 amendment-doc gap as normal, and because the
        # all-payload SHAPE is the real claim -- a fixture that quietly lost its sections
        # some other way would also present zero in-scope sections, and a skip could not
        # tell the two apart. 10 of the 43 committed fixtures take this branch.
        assert payload > 0, (
            f"{test_id}: no sections to check and none are amendment payload either "
            f"({empty} empty). This fixture asserts nothing; it has lost its sections."
        )
        return

    bill_tree = parsed_bill(xml_path)
    node_ids = {node.element_id for node in bill_tree.nodes if node.element_id}

    # Fail CLOSED on a section the gate cannot key on. Skipping it would make a future
    # bill's id-less sections invisible to this check exactly when they stop being
    # covered -- the gate would go green having quietly stopped looking at them.
    # No committed fixture has one; test_idless_section_fails_closed proves this fires.
    idless = [s for s in in_scope if not s.attrib.get("id")]
    assert not idless, (
        f"{test_id}: {len(idless)} of {len(in_scope)} in-scope sections carry no id, so this gate "
        f"cannot verify them. Give the gate another key rather than letting them pass unchecked. "
        f"Sample: {[''.join(s.itertext())[:60] for s in idless[:3]]}"
    )

    missing = [s for s in in_scope if not _section_reaches_a_node(s, node_ids)]
    assert not missing, (
        f"{test_id}: {len(missing)} of {len(in_scope)} enacted sections reach no node "
        f"(payload excluded: {payload}, empty: {empty}). "
        f"Sample: {[(s.attrib.get('id', ''), ''.join(s.itertext())[:60]) for s in missing[:3]]}"
    )


def test_section_coverage_gate_sees_the_corpus() -> None:
    """Fail-closed floor for ``test_every_section_reaches_a_node``.

    That gate excludes amendment payload, and its all-payload branch passes without
    checking anything. Both are correct, and together they leave one way for it to go
    green while asserting nothing: if the payload classification ever widened to swallow
    ordinary sections, every case would take the empty branch and the suite would stay
    green with zero sections verified.

    This counts the in-scope sections the corpus presents, without invoking the parser,
    so it measures the gate's INPUT rather than its verdict -- a parser regression cannot
    move it.
    """
    total = 0
    contributing = 0
    for xml_path in ALL_XML_FILES:
        if not xml_path.exists():
            continue
        root = ET.parse(xml_path).getroot()
        try:
            body = find_bill_body(root)
        except ValueError:
            continue
        in_scope, _payload, _empty = _classify_sections(body, _parent_map(root))
        total += len(in_scope)
        contributing += 1 if in_scope else 0

    assert total >= _MIN_CORPUS_SECTIONS, (
        f"only {total} in-scope sections corpus-wide (floor {_MIN_CORPUS_SECTIONS}); "
        f"test_every_section_reaches_a_node is barely checking anything"
    )
    assert contributing >= 25, f"only {contributing} fixtures present an enacted section to check"


def test_idless_section_fails_closed(tmp_path: Path) -> None:
    """The id-less branch above goes RED rather than passing the section over.

    No committed fixture carries an id-less section, so that assertion would otherwise
    never execute, and an assertion that has never once fired is indistinguishable from
    one that cannot. This builds the case the corpus does not supply.
    """
    xml_path = tmp_path / "1_reported-in-house.xml"
    xml_path.write_text(
        "<bill><legis-body>"
        '<section id="id-s1"><enum>1.</enum><text>With an id.</text></section>'
        "<section><enum>2.</enum><text>Without an id.</text></section>"
        "</legis-body></bill>"
    )

    with pytest.raises(AssertionError, match="carry no id"):
        test_every_section_reaches_a_node(xml_path)


def test_appropriations_section_relaxation_still_fails_closed(tmp_path: Path) -> None:
    """The account-bearing branch of ``_section_reaches_a_node`` cannot wave a loss through.

    That branch lets a section with no text of its own be represented by its accounts
    instead of by a node of its own (#485). A relaxation is only safe if it still goes
    RED on the loss the gate exists to catch, and this is the case the corpus cannot
    supply: 118-hr-9468 is its only untitled appropriations bill, and there the accounts
    do reach nodes, so the false arm of that ``any(...)`` never executes on real fixtures.

    An accounts-only section whose accounts reach NO node is exactly the pre-#485
    behaviour, so this is the shape a regression would take.
    """
    section = ET.fromstring(
        '<section id="sec-1">'
        '<appropriations-major id="maj-1"><header>Department Of Example</header></appropriations-major>'
        '<appropriations-small id="acct-1"><text>For an additional amount, $1,000.</text></appropriations-small>'
        "</section>"
    )

    # The account reached a node: represented, even though the section itself did not.
    assert _section_reaches_a_node(section, {"acct-1"})
    # The section itself reached a node: the ordinary path, unaffected by the relaxation.
    assert _section_reaches_a_node(section, {"sec-1"})
    # Nothing beneath it reached a node — the collapse. Must be reported as missing.
    assert not _section_reaches_a_node(section, {"some-other-section"})
    # A non-appropriations child cannot stand in for the section (the relaxation is
    # scoped to accounts; a subsection reaching a node is not evidence for this shape).
    plain = ET.fromstring('<section id="sec-2"><subsection id="sub-1"><text>x</text></subsection></section>')
    assert not _section_reaches_a_node(plain, {"sub-1"})


def test_an_account_cannot_stand_in_for_a_section_that_has_its_own_text() -> None:
    """A section holding BOTH prose and accounts must still reach its own node.

    This is the case the stand-in must not swallow. The parser emits a node for such a
    section, carrying the prose, so the section's absence from the tree is a real loss of
    the only copy of that text — and it is invisible to the money gates, because the
    accounts and their dollar figures survive in their own nodes either way.

    Without the ``_extract_section_text`` guard the gate accepted any surviving account
    as proof the section arrived, so a regression that dropped the section node while
    keeping ``acct-1`` passed green with the section-level prose gone.
    """
    section = ET.fromstring(
        '<section id="sec-1">'
        "<text>section-level prose</text>"
        '<appropriations-small id="acct-1"><text>$1,000</text></appropriations-small>'
        "</section>"
    )

    assert not _section_reaches_a_node(section, {"acct-1"})
    # Only the section's own node answers for it, which is the pre-#485 rule unchanged
    # for every section that has a body.
    assert _section_reaches_a_node(section, {"sec-1"})

    # The distinction is the section's OWN text, not the presence of a <text> element:
    # a section whose only text lives inside its accounts still has an empty own body,
    # so it keeps the stand-in.
    accounts_only = ET.fromstring(
        '<section id="sec-2">'
        "<enum>2.</enum><header>Ignored by the extractor</header>"
        '<appropriations-small id="acct-2"><text>$1,000</text></appropriations-small>'
        "</section>"
    )
    assert _section_reaches_a_node(accounts_only, {"acct-2"})


def test_bare_division_sections_emit_their_appropriations_accounts() -> None:
    """The ``division > section > appropriations-*`` shape, which no local bill supplies.

    ``walk_body_sections`` serves two arrangements: sections directly under the bill body,
    and the bare sections of a division that does not wrap them in a <title> (#465). #485
    recorded the division form as untested because the corpus contains no such bill, so
    the fix's reach into it rested on the two callers sharing one function rather than on
    a measurement. This builds the case synthetically and checks it directly.

    The division label must also travel onto the account nodes, since that is what the
    diff groups on (#468); an account that arrived without it would be addressable but
    would not group with the division it belongs to.
    """
    division = ET.fromstring(
        '<division id="div-a">'
        "<enum>A</enum><header>Example Division</header>"
        '<section id="sec-1">'
        '<appropriations-major id="maj-1"><header>Department Of Example</header></appropriations-major>'
        '<appropriations-small id="acct-1"><header>Salaries And Expenses</header>'
        "<text>For necessary expenses, $1,000.</text></appropriations-small>"
        "</section>"
        "</division>"
    )
    div = Division(label="Division A: Example Division", key="a")

    nodes = walk_body_sections(division, div)

    accounts = [n for n in nodes if n.tag == "appropriations-small"]
    assert len(accounts) == 1, f"the division's account reached no node of its own: {[n.match_path for n in nodes]}"
    account = accounts[0]
    assert account.header_text == "Salaries And Expenses"
    assert "$1,000" in account.body_text
    # Addressed off its agency, exactly as on the body-level path. The division is
    # excluded from match_path everywhere, so it does not appear here.
    assert account.match_path == ("department of example", "salaries and expenses")
    assert account.division_label == "Division A: Example Division"
    assert account.division_key == "a"
    # The accounts-only section contributes no node of its own, same as at body level.
    assert not [n for n in nodes if n.element_id == "sec-1"]
