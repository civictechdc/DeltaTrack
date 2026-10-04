"""Answer key for the section-matching similarity thresholds (DeltaTrack #8).

`tests/data/similarity_labels.json` is a hand-labeled set of real section pairs, each
tagged SAME (a revision of one provision -> the matcher should link them) or DIFFERENT
(unrelated provisions that merely share boilerplate -> should not be linked). We run the
production similarity pipeline (`text_similarity(_normalize_text(...))`) over the frozen
text and check whether the governing threshold classifies each pair the way a human did.

Three kinds of pairs:

  * Clear-cut anchors (extreme similarity, self-evident label). These MUST classify
    correctly; a failure is a real regression in the thresholds or the similarity code.
  * Five human-ruled dead-zone pairs (0.40-0.63 band). The current thresholds get all
    five wrong, so they are `xfail(strict=False)`: they pin the failure mode as an
    executable spec and flip to XPASS if the thresholds are ever improved (#170). Their
    `expected_misclassified` flag in the fixture is the frozen finding.
  * One extreme clear-cut miss (119-hr-1 Alien SNAP, a stub expanded to full text,
    sim 0.078). High-confidence SAME, not a judgment call, yet far below the 0.40 floor,
    so it too is `xfail`. It is the #8-body anchor for the stub->expanded failure mode.

Body-text-only ON PURPOSE. The dead-zone misses are the evidence for #170: pure text
similarity has no skill there, and the disambiguating signal is structural context (the
division > agency > account breadcrumb), not more text. Pairs 4 and 5 are the worked
examples in that issue. Self-contained: no `bills/` dependency; regenerate the fixture
with `scripts/build_similarity_labels.py` if the corpus text or thresholds change.
"""

from __future__ import annotations

import json

import pytest

from deltatrack.diff_bill import _normalize_text
from deltatrack.similarity import (
    MOVE_THRESHOLD,
    SIMILARITY_THRESHOLD,
    text_similarity,
)
from tests.corpus_paths import DATA_DIR

_FIXTURE = DATA_DIR / "similarity_labels.json"
_PAIRS = json.loads(_FIXTURE.read_text(encoding="utf-8"))["pairs"]


def _threshold(decision: str) -> float:
    """The similarity cutoff that governs this pair's decision."""
    return SIMILARITY_THRESHOLD if decision == "split" else MOVE_THRESHOLD


def _predicted_label(pair: dict) -> str:
    """What the current thresholds decide for this pair: 'same' or 'different'."""
    sim = text_similarity(_normalize_text(pair["text_old"]), _normalize_text(pair["text_new"]))
    return "same" if sim >= _threshold(pair["decision"]) else "different"


def _param(pair: dict):
    """One parametrize entry; dead-zone misses carry a non-strict xfail marker."""
    marks = ()
    if pair["expected_misclassified"]:
        marks = pytest.mark.xfail(
            reason=f"known dead-zone miss (#170): {pair['rationale']}",
            strict=False,
        )
    return pytest.param(pair, marks=marks, id=pair["id"])


@pytest.mark.parametrize("pair", [_param(p) for p in _PAIRS])
def test_threshold_matches_human_label(pair: dict) -> None:
    """The governing threshold classifies each pair the way the human label says.

    Anchors must pass. The five dead-zone pairs are expected to fail (xfail); if a
    threshold change makes one pass it surfaces as XPASS, prompting a fixture update.
    """
    predicted = _predicted_label(pair)
    assert predicted == pair["label"], (
        f"{pair['id']}: threshold {_threshold(pair['decision'])} for a {pair['decision']} "
        f"decision predicted {predicted!r}, human label is {pair['label']!r}"
    )


def test_fixture_is_well_formed() -> None:
    """Guard against a silently empty or malformed answer key (fail-open protection)."""
    assert len(_PAIRS) == 12, "expected 12 labeled pairs; fixture drifted"
    contested = [p for p in _PAIRS if p["source"] == "contested"]
    anchors = [p for p in _PAIRS if p["source"] == "anchor"]
    extreme = [p for p in _PAIRS if p["source"] == "extreme"]
    assert len(contested) == 5 and len(anchors) == 6 and len(extreme) == 1
    for p in _PAIRS:
        assert p["source"] in ("contested", "anchor", "extreme")
        assert p["label"] in ("same", "different")
        assert p["decision"] in ("split", "move")
        assert p["text_old"] and p["text_new"]
    # Contested (dead-zone rulings) and the extreme miss are known-wrong; anchors are right.
    assert all(p["expected_misclassified"] for p in contested)
    assert all(p["expected_misclassified"] for p in extreme)
    assert not any(p["expected_misclassified"] for p in anchors)


def _confusion(pairs: list[dict], decision: str) -> dict[str, int]:
    """Confusion matrix for the SAME class over pairs governed by one threshold."""
    tp = fp = fn = tn = 0
    for p in pairs:
        if p["decision"] != decision:
            continue
        predicted_same = _predicted_label(p) == "same"
        label_same = p["label"] == "same"
        if predicted_same and label_same:
            tp += 1
        elif predicted_same and not label_same:
            fp += 1
        elif not predicted_same and label_same:
            fn += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def test_split_threshold_precision_recall() -> None:
    """Frozen precision/recall of the 0.40 split floor against the labels.

    Guardrail values, not vanity metrics: they fail loudly if any split pair's
    classification shifts. precision(same)=0.40, recall(same)=0.50 today -- three false
    keeps (boilerplate over the floor) and two false splits (a long added proviso, and a
    stub->expanded rewrite) are exactly the #170 finding. Recompute if the fixture or 0.40
    changes.
    """
    c = _confusion(_PAIRS, "split")
    assert c == {"tp": 2, "fp": 3, "fn": 2, "tn": 2}, f"split-threshold confusion matrix changed: {c}"
    precision = c["tp"] / (c["tp"] + c["fp"])
    recall = c["tp"] / (c["tp"] + c["fn"])
    assert precision == pytest.approx(0.40)
    assert recall == pytest.approx(0.50)


def test_move_threshold_precision_recall() -> None:
    """Frozen precision/recall of the 0.60 move threshold against the labels.

    Two genuine relocations (identical text, moved location) anchor the true positives;
    the one false move (Ag->HHS Medicare, boilerplate alone clears 0.60) is the false
    positive that drags precision down. precision(same)=0.667, recall(same)=1.0 today.
    Move coverage is still thin (three labeled pairs); expand it for a tighter metric.
    """
    c = _confusion(_PAIRS, "move")
    assert c == {"tp": 2, "fp": 1, "fn": 0, "tn": 0}, f"move-threshold confusion matrix changed: {c}"
    precision = c["tp"] / (c["tp"] + c["fp"])
    recall = c["tp"] / (c["tp"] + c["fn"])
    assert precision == pytest.approx(2 / 3)
    assert recall == pytest.approx(1.0)
