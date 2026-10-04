"""Regenerate tests/data/similarity_labels.json — the similarity-threshold answer key (#8).

Pulls each labeled pair's exact body text straight from the real corpus diffs so the
frozen fixture text is byte-identical to what the matcher sees, then freezes text + the
human SAME/DIFFERENT labels + the current-threshold finding (which pairs today's 0.40/0.60
cutoffs misclassify). The committed JSON is self-contained; this script only exists to
re-freeze it if the corpus text or the threshold constants change. Reads the committed
fixtures under tests/corpus/, so it needs no download.

The five human-ruled dead-zone pairs and their labels come from DeltaTrack #8 (Will's
rulings, 2026-07-10); the four clear-cut anchors are mined from the corpus to keep
precision/recall non-degenerate. Body-text-only is deliberate — see the fixture `_about`
and #170.

Usage (from DeltaTrack root, uv env):
    uv run python scripts/build_similarity_labels.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from deltatrack.bill_tree import normalize_bill  # noqa: E402
from deltatrack.diff_bill import _normalize_text, diff_bills  # noqa: E402
from deltatrack.similarity import MOVE_THRESHOLD, SIMILARITY_THRESHOLD, text_similarity  # noqa: E402
from tests.corpus_paths import DATA_DIR, fixture_path  # noqa: E402

_OUT = DATA_DIR / "similarity_labels.json"


def _diff(bill: str, v_old: str, v_new: str):
    return diff_bills(normalize_bill(fixture_path(bill, v_old)), normalize_bill(fixture_path(bill, v_new)))


def _find(d, match_path, change_type):
    """The single change at `match_path` with `change_type` (modified/removed/added)."""
    mp = tuple(match_path)
    for c in d.changes:
        if tuple(c.match_path) == mp and c.change_type == change_type:
            return c
    raise LookupError(f"{change_type} {mp} not found")


def _find_move(d, needle: str):
    """The moved change whose old text contains `needle`."""
    for c in d.changes:
        if c.change_type == "moved" and c.old_text and needle in c.old_text:
            return c
    raise LookupError(f"move containing {needle!r} not found")


def _threshold(decision: str) -> float:
    return SIMILARITY_THRESHOLD if decision == "split" else MOVE_THRESHOLD


def _build_specs() -> list[dict]:
    """The 9 pairs: 5 human-ruled dead-zone pairs + 4 clear-cut anchors.

    `decision` selects the governing threshold: "split" -> 0.40 floor, "move" -> 0.60.
    A pair supplies its change either as `c` (a single NodeDiff) or as explicit
    old/new text + paths (for the split pairs surfaced as separate remove+add).
    """
    specs: list[dict] = []

    # Pair 1 — 114-hr-2029 sec. 232 (VA admin), modified, ~0.429. Ruled DIFFERENT.
    d = _diff("114-hr-2029", "5_engrossed-amendment-senate.xml", "6_engrossed-amendment-house.xml")
    c = _find(d, ("department of veterans affairs", "administrative provisions", "sec. 232"), "modified")
    specs.append(
        dict(
            id="contested-1-va-232",
            source="contested",
            bill="114-hr-2029",
            version_old="5_engrossed-amendment-senate",
            version_new="6_engrossed-amendment-house",
            decision="split",
            change_type="modified",
            c=c,
            label="different",
            rationale=(
                "Old requires 15-day notice before transferring 25+ FTEs; new requires quarterly "
                "notice of marketing campaigns over $2M. Only notification boilerplate is shared, "
                "so these are unrelated provisions. Similarity ~0.43 sits just above the 0.40 split "
                "floor, so the tool wrongly keeps the match (false keep)."
            ),
        )
    )

    # Pair 2 — 115-hr-5895 sec. 110 (Corps GP), modified, ~0.447. Ruled DIFFERENT.
    d = _diff("115-hr-5895", "4_engrossed-amendment-senate.xml", "5_enrolled-bill.xml")
    c = _find(d, ("corps of engineers—civil", "general provisions—corps of engineers—civil", "sec. 110"), "modified")
    specs.append(
        dict(
            id="contested-2-corps-110",
            source="contested",
            bill="115-hr-5895",
            version_old="4_engrossed-amendment-senate",
            version_new="5_enrolled-bill",
            decision="split",
            change_type="modified",
            c=c,
            label="different",
            rationale=(
                "Both open 'None of the funds...' but old bars releasing water from Lake Okeechobee "
                "while new bars reorganizing the Corps' Civil Works functions. Shared appropriations "
                "boilerplate inflates the word ratio to ~0.45, just above the 0.40 floor, so the tool "
                "wrongly keeps the match (false keep)."
            ),
        )
    )

    # Pair 3 — 115-hr-5895 sec. 204 (Interior GP), modified, ~0.462. Ruled DIFFERENT.
    d = _diff("115-hr-5895", "4_engrossed-amendment-senate.xml", "5_enrolled-bill.xml")
    c = _find(
        d, ("department of the interior", "general provisions—department of the interior", "sec. 204"), "modified"
    )
    specs.append(
        dict(
            id="contested-3-interior-204",
            source="contested",
            bill="115-hr-5895",
            version_old="4_engrossed-amendment-senate",
            version_new="5_enrolled-bill",
            decision="split",
            change_type="modified",
            c=c,
            label="different",
            rationale=(
                "Old amends the Omnibus Public Land Management Act ('10'->'20'); new amends the Fort "
                "Peck Reservation Rural Water System Act (2020->2026). Different underlying statutes; "
                "the shared words are 'is amended by striking...' boilerplate. Similarity ~0.46 is "
                "above the 0.40 floor, so the tool wrongly keeps the match (false keep)."
            ),
        )
    )

    # Pair 4 — 118-hr-4366 Tanker Security Program (DOT/Maritime), split, ~0.255. Ruled SAME.
    d = _diff("118-hr-4366", "4_engrossed-amendment-senate.xml", "5_engrossed-amendment-house.xml")
    rc = _find(d, ("department of transportation", "maritime administration", "tanker security program"), "removed")
    ac = _find(d, ("department of transportation", "maritime administration", "tanker security program"), "added")
    specs.append(
        dict(
            id="contested-4-tanker",
            source="contested",
            bill="118-hr-4366",
            version_old="4_engrossed-amendment-senate",
            version_new="5_engrossed-amendment-house",
            decision="split",
            change_type="split",
            label="same",
            old_text=rc.old_text,
            new_text=ac.new_text,
            match_path=list(rc.match_path),
            display_path_old=list(rc.display_path_old),
            display_path_new=list(ac.display_path_new),
            rationale=(
                "STRUCTURAL CONTEXT (for #170): same account, same subject. Funding drops $120M->$60M "
                "and a ~5x-longer proviso is appended, which dilutes the word ratio to ~0.26, below the "
                "0.40 floor, so the tool wrongly splits one account into a remove+add (false split). The "
                "identical 'TANKER SECURITY PROGRAM' account heading is the context signal that says keep "
                "them linked. This is the most consequential pair: ruling SAME means 0.40 is too high for "
                "an account that merely gains a long proviso."
            ),
        )
    )

    # Pair 5 — 118-hr-4366 Ag sec. 775 -> Div G HHS Medicare sec. 309, moved, ~0.629. Ruled DIFFERENT.
    d = _diff("118-hr-4366", "4_engrossed-amendment-senate.xml", "5_engrossed-amendment-house.xml")
    c = _find_move(d, "542(b)(2) of the Housing Act")
    specs.append(
        dict(
            id="contested-5-ag-to-hhs",
            source="contested",
            bill="118-hr-4366",
            version_old="4_engrossed-amendment-senate",
            version_new="5_engrossed-amendment-house",
            decision="move",
            change_type="moved",
            c=c,
            label="different",
            rationale=(
                "STRUCTURAL CONTEXT (for #170): old amends the Housing Act (542(b)(2), 5,000->10,000) in "
                "Division B Agriculture; new amends the Social Security Act (Medicare 1898(b)(1), "
                "$2.197B->$0) in Division G HHS. Completely different statutes and subjects. Only "
                "'Section ... is amended by striking ...' boilerplate is shared, and that alone lifts the "
                "ratio to ~0.63, clearing the 0.60 move threshold, so the tool wrongly links them across "
                "divisions (false move). The division/agency breadcrumb (Ag vs HHS Medicare) is the "
                "context signal that says do not link."
            ),
        )
    )

    # --- Clear-cut anchors (extreme similarity, self-evident label, no dead-zone judgment). ---
    d = _diff("115-hr-5895", "4_engrossed-amendment-senate.xml", "5_enrolled-bill.xml")
    c = _find(
        d,
        ("legislative branch", "library of congress", "congressional research service", "salaries and expenses"),
        "modified",
    )
    specs.append(
        dict(
            id="anchor-same-crs",
            source="anchor",
            bill="115-hr-5895",
            version_old="4_engrossed-amendment-senate",
            version_new="5_enrolled-bill",
            decision="split",
            change_type="modified",
            c=c,
            label="same",
            rationale=(
                "Near-identical revision of the CRS salaries account (~0.99); "
                "unambiguously the same provision. Correctly kept."
            ),
        )
    )

    d = _diff("118-hr-4366", "4_engrossed-amendment-senate.xml", "5_engrossed-amendment-house.xml")
    c = _find(d, ("general provisions", "sec. 716", "(d)"), "modified")
    specs.append(
        dict(
            id="anchor-same-sec716d",
            source="anchor",
            bill="118-hr-4366",
            version_old="4_engrossed-amendment-senate",
            version_new="5_engrossed-amendment-house",
            decision="split",
            change_type="modified",
            c=c,
            label="same",
            rationale=(
                "Near-identical revision of general provision sec. 716(d) (~0.99); "
                "unambiguously the same provision. Correctly kept."
            ),
        )
    )

    d = _diff("118-hr-4366", "4_engrossed-amendment-senate.xml", "5_engrossed-amendment-house.xml")
    rc = _find(d, ("general provisions", "sec. 780"), "removed")
    ac = _find(d, ("general provisions", "sec. 780"), "added")
    specs.append(
        dict(
            id="anchor-diff-sec780",
            source="anchor",
            bill="118-hr-4366",
            version_old="4_engrossed-amendment-senate",
            version_new="5_engrossed-amendment-house",
            decision="split",
            change_type="split",
            label="different",
            old_text=rc.old_text,
            new_text=ac.new_text,
            match_path=list(rc.match_path),
            display_path_old=list(rc.display_path_old),
            display_path_new=list(ac.display_path_new),
            rationale=(
                "Section number 780 reused for unrelated provisions (Ag facility grants "
                "vs Rural Electrification Act amendment); ~0.15, unambiguously different. "
                "Correctly split."
            ),
        )
    )

    d = _diff("115-hr-5895", "4_engrossed-amendment-senate.xml", "5_enrolled-bill.xml")
    rc = _find(d, ("department of veterans affairs", "administrative provisions", "sec. 252"), "removed")
    ac = _find(d, ("department of veterans affairs", "administrative provisions", "sec. 252"), "added")
    specs.append(
        dict(
            id="anchor-diff-sec252",
            source="anchor",
            bill="115-hr-5895",
            version_old="4_engrossed-amendment-senate",
            version_new="5_enrolled-bill",
            decision="split",
            change_type="split",
            label="different",
            old_text=rc.old_text,
            new_text=ac.new_text,
            match_path=list(rc.match_path),
            display_path_old=list(rc.display_path_old),
            display_path_new=list(ac.display_path_new),
            rationale=(
                "Section number 252 reused for unrelated VA provisions (report requirement "
                "vs VHA funds restriction); ~0.20, unambiguously different. Correctly split."
            ),
        )
    )

    # True-move anchors: identical provisions that genuinely relocated. They give the
    # 0.60 move threshold true positives so its precision/recall is non-degenerate, and
    # they are the clean contrast to contested-5 (a false move across divisions).
    d = _diff("118-hr-4366", "4_engrossed-amendment-senate.xml", "5_engrossed-amendment-house.xml")
    c = _find_move(d, "The Secretary shall comply with all process requirements")
    specs.append(
        dict(
            id="anchor-move-hud-237",
            source="anchor",
            bill="118-hr-4366",
            version_old="4_engrossed-amendment-senate",
            version_new="5_engrossed-amendment-house",
            decision="move",
            change_type="moved",
            c=c,
            label="same",
            rationale=(
                "Identical HUD annual-contributions provision relocated from Division C "
                "sec. 237 to Division F sec. 234 (sim 1.00). A genuine cross-division move "
                "that should link; correctly linked at the 0.60 threshold."
            ),
        )
    )

    d = _diff("118-hr-4366", "4_engrossed-amendment-senate.xml", "5_engrossed-amendment-house.xml")
    c = _find_move(d, "closure or realignment of the United")
    specs.append(
        dict(
            id="anchor-move-dod-135",
            source="anchor",
            bill="118-hr-4366",
            version_old="4_engrossed-amendment-senate",
            version_new="5_engrossed-amendment-house",
            decision="move",
            change_type="moved",
            c=c,
            label="same",
            rationale=(
                "Identical DoD Guantanamo base-closure provision renumbered from sec. 135 to "
                "sec. 138 within Division A (sim 1.00). A genuine move that should link; "
                "correctly linked at the 0.60 threshold."
            ),
        )
    )

    # Extreme clear-cut miss: a high-confidence SAME label (no dead-zone judgment) that the
    # current signal gets badly wrong. 119-hr-1 Sec. 10012 Alien-SNAP-eligibility is an
    # 81-char stub in the reported version and 2,242 chars of expanded text in engrossed, so
    # the correct pair scores 0.078 -- far below the 0.40 floor -- and the tool false-splits
    # it. Requested in the #8 body as the anchor for the stub->expanded failure mode.
    d = _diff("119-hr-1", "1_reported-in-house.xml", "2_engrossed-in-house.xml")
    rc = _find(d, ("committee on agriculture", "nutrition", "sec. 10012"), "removed")
    ac = _find(d, ("committee on agriculture", "nutrition", "sec. 10012"), "added")
    specs.append(
        dict(
            id="extreme-alien-snap-10012",
            source="extreme",
            bill="119-hr-1",
            version_old="1_reported-in-house",
            version_new="2_engrossed-in-house",
            decision="split",
            change_type="split",
            label="same",
            old_text=rc.old_text,
            new_text=ac.new_text,
            match_path=list(rc.match_path),
            display_path_old=list(rc.display_path_old),
            display_path_new=list(ac.display_path_new),
            rationale=(
                "Same provision: Section 6(f) of the Food and Nutrition Act of 2008, an 81-char "
                "stub in reported expanded to 2,242 chars in engrossed. High-confidence SAME, yet "
                "the body representation change drops similarity to 0.078, far below the 0.40 "
                "floor, so the tool false-splits it (an extreme of contested-4). The identical "
                "section header ('Alien SNAP eligibility') is the signal that would keep it linked "
                "-- the workstream-2 evidence handed to #170."
            ),
        )
    )

    return specs


def _to_record(s: dict) -> dict:
    if "c" in s:
        c = s["c"]
        old_text, new_text = c.old_text, c.new_text
        match_path = list(c.match_path)
        display_path_old = list(c.display_path_old) if c.display_path_old else None
        display_path_new = list(c.display_path_new) if c.display_path_new else None
    else:
        old_text, new_text = s["old_text"], s["new_text"]
        match_path = s["match_path"]
        display_path_old, display_path_new = s["display_path_old"], s["display_path_new"]

    sim = text_similarity(_normalize_text(old_text), _normalize_text(new_text))
    predicted = "same" if sim >= _threshold(s["decision"]) else "different"

    return {
        "id": s["id"],
        "source": s["source"],
        "bill": s["bill"],
        "version_old": s["version_old"],
        "version_new": s["version_new"],
        "match_path": match_path,
        "display_path_old": display_path_old,
        "display_path_new": display_path_new,
        "change_type": s["change_type"],
        "decision": s["decision"],
        "label": s["label"],
        "expected_misclassified": predicted != s["label"],
        "text_old": old_text,
        "text_new": new_text,
        "rationale": s["rationale"],
        "_observed_similarity": round(sim, 3),  # informational; the test recomputes live
    }


def main() -> None:
    records = [_to_record(s) for s in _build_specs()]
    payload = {
        "_about": (
            "Hand-labeled answer key for the section-matching similarity thresholds "
            "(DeltaTrack #8). 5 human-ruled dead-zone pairs (0.40-0.63 band, all currently "
            "misclassified), 6 clear-cut anchors (2 near-identical keeps, 2 unrelated splits, "
            "2 genuine relocations), and 1 extreme clear-cut miss (Alien SNAP stub->expanded, "
            "0.078, far below the floor). Body-text-only ON PURPOSE: it measures "
            "that pure text similarity fails in the dead zone, which is the evidence for #170 "
            "(structural context as the disambiguating signal). Thresholds under test: split "
            "floor SIMILARITY_THRESHOLD=0.40, move MOVE_THRESHOLD=0.60 in diff_bill.py. "
            "text_old/text_new are frozen verbatim from the corpus; the test recomputes "
            "similarity live via text_similarity(_normalize_text(...)). Regenerate with "
            "scripts/build_similarity_labels.py."
        ),
        "pairs": records,
    }
    _OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {_OUT.relative_to(_ROOT)} ({len(records)} pairs)")
    for r in records:
        flag = "MISCLASSIFIED" if r["expected_misclassified"] else "ok"
        print(f"  {r['id']:26s} {r['decision']:5s} sim={r['_observed_similarity']:.3f} label={r['label']:9s} {flag}")
    contested_mis = sum(r["expected_misclassified"] for r in records if r["source"] == "contested")
    anchor_mis = sum(r["expected_misclassified"] for r in records if r["source"] == "anchor")
    assert contested_mis == 5, f"expected all 5 contested pairs misclassified, got {contested_mis}"
    assert anchor_mis == 0, f"expected 0 anchors misclassified, got {anchor_mis}"


if __name__ == "__main__":
    main()
