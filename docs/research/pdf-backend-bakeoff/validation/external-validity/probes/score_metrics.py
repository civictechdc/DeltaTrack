"""score_metrics -- HARNESS-PLAN section 5. The frozen metric scorer, and NOTHING else.

    owns      PRE-REGISTRATION section 6 (M0-M9 minus M6) and section 8 (via A27.5/A37)
    consumes  committed artifacts ONLY -- frames, oracle key, oracle adjudicated,
              `cross_engine_control.json`, `s1_control.json`, and the committed membership's
              stratum labels
    tests     `x27_score_metrics.py` (SYNTHETIC + DEVELOPMENT only)

WHAT THIS MODULE MAY NOT DO, stated first because the boundary is the point.

It does not run H, run X, open a PDF, select a frame, rebuild a population, reconstruct XML or
PDF source truth, adjudicate anything, run historical repo discovery, or take the architecture
decision. Every fact it needs was made reachable from committed artifacts by A38, and A38.1's
ownership table is the map. `decide_architecture` owns section 7; the Rule 0 / Rule 1 / Rule 3
outcomes are ABSENT here on purpose -- this module emits the facts those rules read, and a
reader can check that no outcome enum appears in what it writes.

NOTHING HERE IS A NEW METHODOLOGICAL RULE. Where a frozen rule already has an executable owner
this module CALLS it rather than restating it, because two copies of a rule are two rules:

    M0 block                `neutral_identity.m0`               (A22/A23)
    M3 boundaries/outcome   `m3_boundaries.heading_outcome`     (A3/A4/section 6.3)
    M2 normalisation        `xml_sources.normalize`             (section 6.2)
    M5 role coarsening      `methodology_contracts.m5_*`        (A36.7)
    section 4.5 adequacy    `methodology_contracts.adequacy`    (A28.1/A28.2/A30.4)
    section 8 zero event    `methodology_contracts.zero_event_upper_bound`
    section 8 bootstrap     `methodology_contracts.section8_document_bootstrap` (A37, A38.10)
    Rule 0 margin clause    `methodology_contracts.margin_line_loss`            (A39.1)
    occurrence join         `build_oracle.resolve_adjudicated_occurrence`       (A38.7)
    adjudication encoding   `build_oracle.validate_adjudicated`                 (A38.7)
    answer routing          `build_oracle.PURPOSE_ROUTE`                        (A36.4)
    cross-engine gate       `cross_engine_control.json`, produced by `X09.gate` (A39.2)

`build_oracle` is imported LAZILY (see `_bo`) because it imports `pymupdf` at module scope.
Consuming committed JSON must not require a PDF renderer merely to import this module; the
delegation itself is unchanged, and `x27` proves the property in a child interpreter where the
renderer cannot be imported at all.

The only quantity this module implements from scratch is the general one-sided
Clopper-Pearson upper bound, which A27.5 assigns to it explicitly and which
`methodology_contracts` deliberately does not carry.

MALFORMED INPUT REFUSES. Every refusal below exists because the alternative -- skipping a
record -- moves a denominator with nothing to show for it, and a smaller denominator reads as a
cleaner result. The refusals are deterministic and carry the offending record.
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve()
EV = HERE.parents[1]
BAKE = EV.parents[1]
REPO = BAKE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(BAKE / "probes"))
sys.path.insert(0, str(BAKE / "probes" / "backends"))

import build_frames as BF  # noqa: E402
import m3_boundaries as M3  # noqa: E402
import methodology_contracts as MC  # noqa: E402
import neutral_identity as NI  # noqa: E402
from xml_sources import normalize as m2_normalize  # noqa: E402

SCHEMA = "metrics/1"
ARMS = ("H", "X")

#: `build_oracle`, resolved on first use. NOT imported at module scope -- see `_bo`.
_BO = None


def _bo():
    """The frozen oracle-side owners (A38.7 join, A38.7 encoding, A36.4 routing), LAZILY.

    `build_oracle` imports `pymupdf` at module scope, because rendering the adjudication stimuli
    is part of what it owns. The scorer renders nothing: A38 exists precisely so every fact it
    needs is reachable from committed JSON. Importing those helpers eagerly would therefore make
    a PDF renderer a hard requirement of *importing the scorer*, re-acquiring at the import line
    the dependency A38 removed from the data path -- and it is why the scorer could not be
    imported at all in a renderer-free environment.

    Deferring the import to the call sites that genuinely need the frozen helpers keeps the
    delegation intact. This is deliberately NOT a local copy of those rules: two copies of a rule
    are two rules, so a caller that needs the A38.7 join still gets `build_oracle`'s, and still
    fails loudly if the renderer is genuinely absent when that join is required.
    """
    global _BO
    if _BO is None:
        import build_oracle

        _BO = build_oracle
    return _BO


# ---------------------------------------------------------------------- refusal reasons

FRAMES_NOT_A_SEQUENCE = "FRAMES_NOT_A_SEQUENCE"
MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
#: A47.12 -- present but NOT the section 4.7 required value.
WRONG_CONFIRMATORY_STATUS = "WRONG_CONFIRMATORY_STATUS"
#: A48 -- the key's A27.3 claim disagrees with the committed frames or the frozen predicate.
D_CENSUS_MISSING = "D_CENSUS_MISSING"
D_CENSUS_MISMATCH = "D_CENSUS_MISMATCH"
D_BUDGET_CLAIM_MISMATCH = "D_BUDGET_CLAIM_MISMATCH"
DUPLICATE_DOCUMENT_IDENTITY = "DUPLICATE_DOCUMENT_IDENTITY"
UNKNOWN_POPULATION = "UNKNOWN_POPULATION"
UNKNOWN_LINE_STATE = "UNKNOWN_LINE_STATE"
LINE_STATE_PREDICATE_DRIFT = "LINE_STATE_PREDICATE_DRIFT"
ANCHOR_EVIDENCE_DRIFT = "ANCHOR_EVIDENCE_DRIFT"
D_FRAME_ELIGIBILITY_DRIFT = "D_FRAME_ELIGIBILITY_DRIFT"
#: `build_frames` emits `d_frame = bool(reasons)` from the same expression that builds `d_reasons`,
#: so the two can only disagree if something rewrote one of them. Checked exactly, because the
#: line-level and anchor checks each look at ONE predicate and cannot see a broken relationship
#: between the flag and the reason list as a whole.
D_FRAME_FLAG_DRIFT = "D_FRAME_FLAG_DRIFT"
COVERAGE_FLOOR_DRIFT = "COVERAGE_FLOOR_DRIFT"
OCCURRENCE_RECORD_INCOMPLETE = "OCCURRENCE_RECORD_INCOMPLETE"
DUPLICATE_OCCURRENCE_KEY = "DUPLICATE_OCCURRENCE_KEY"
STIMULUS_DOCUMENT_NOT_IN_FRAMES = "STIMULUS_DOCUMENT_NOT_IN_FRAMES"
ADJUDICATION_ROUTE_MISSING = "ADJUDICATION_ROUTE_MISSING"
CONTROL_STIMULUS_IN_ESTIMAND = "CONTROL_STIMULUS_IN_ESTIMAND"
CROSS_ENGINE_DOCUMENT_MISSING = "CROSS_ENGINE_DOCUMENT_MISSING"
#: I13's consequence counts DOCUMENTS, so the cross-engine artifact's document set is a
#: denominator and must equal the scored set exactly -- see `validate_inputs`.
CROSS_ENGINE_EXTRA_DOCUMENT = "CROSS_ENGINE_EXTRA_DOCUMENT"
CROSS_ENGINE_DUPLICATE_DOCUMENT = "CROSS_ENGINE_DUPLICATE_DOCUMENT"
#: A row without `document` or `passed` is not a verdict about anything. Refused structurally so it
#: cannot reach `qualification` and become an invented one.
CROSS_ENGINE_ROW_MALFORMED = "CROSS_ENGINE_ROW_MALFORMED"
#: An R1 repeat whose primary is not in the key. The repeat would then have nothing to be a
#: repeat OF, and silently skipping it would shrink the reliability population -- the population
#: whose whole job is to be large enough to detect an unreliable adjudicator.
R1_PRIMARY_MISSING = "R1_PRIMARY_MISSING"
#: A36.6 enforcement. A repeat that declares its own shorter route set could delete a FAILING
#: required route and leave an internally coherent artifact behind, so the required routes are
#: derived from FRAME MEMBERSHIP and the repeat's declaration is checked against them.
R1_FRAME_SET_MISMATCH = "R1_FRAME_SET_MISMATCH"
R1_ROUTE_SET_MISMATCH = "R1_ROUTE_SET_MISMATCH"
S1_ARTIFACT_MISSING = "S1_ARTIFACT_MISSING"
STRATUM_MISSING = "STRATUM_MISSING"
#: A null `role` is not the oracle's UNREADABLE. The codebook is a CLOSED vocabulary with
#: `other` for an unclassifiable heading and `UNREADABLE` for an illegible one, so a null is a
#: missing answer rather than a representable one, and mapping it to UNSCORABLE would shrink the
#: M5 denominator -- which reads as a cleaner result.
ROLE_MISSING = "ROLE_MISSING"
#: Same reasoning for `parent`. The frozen encoding (section 5.3, and `adjudicator_prompt.md`
#: section 3) gives the adjudicator three literals and nothing else: the printed parent text,
#: `NONE`, or `OFF_REGION`, plus `UNREADABLE` for an unresolvable field. A JSON null is none of
#: them, so it is a missing answer -- and reading it as "no parent" is exactly the false green
#: this refusal closes.
PARENT_MISSING = "PARENT_MISSING"

#: The adjudicated `parent` sentinels, frozen by PRE-REGISTRATION section 5.3 ("each heading's
#: parent heading text as printed, or `NONE`, or `OFF_REGION`") and asked for in those words by
#: `adjudicator_prompt.md`. They are LITERAL ANSWERS, never parent text: comparing `"NONE"` as a
#: string would score a root heading wrong, and comparing `"OFF_REGION"` as a string would charge
#: an architecture for the ORACLE's field of view.
ORACLE_PARENT_NONE = "NONE"
ORACLE_PARENT_OFF_REGION = "OFF_REGION"

#: Statuses a metric entry can carry instead of a rate. I11 -- a zero content-bearing
#: denominator is VACUOUS and is never printed as agreement.
VACUOUS = "VACUOUS"
NOT_REPORTABLE_S1_DEAD = "NOT_REPORTABLE_S1_DEAD"

#: I13's reporting qualification. Never blocks -- A27.6 keeps x09 out of the gate vector.
PDFIUM_CONDITIONED_FRAME = "PDFIUM-CONDITIONED FRAME"

#: A47.12 -- the section 4.7 status every A45-dependent result MUST carry. A REQUIREMENT the
#: cross-engine artifact is validated against, never a value it supplies. Held independently of
#: `continuation_provenance` because the allowlist forbids importing it; x30 asserts they agree.
REQUIRED_CONFIRMATORY_STATUS = "NON-CONFIRMATORY (PRE-REGISTRATION 4.7 -- A45 post-boundary deviation)"

#: A48 -- the SAME section 4.7 status CLASS as A45/A47, but its own literal, because the label
#: above names A45 and this deviation is not that one. Applied only where A48 actually changes
#: a value: at D > 60 it changes which routes R1 is required to score, so R1's value can move.
#: At D <= 60 the historical D -> human semantics are untouched and this label is NOT claimed
#: merely because the code contains A48.
A48_NON_CONFIRMATORY = "NON-CONFIRMATORY (PRE-REGISTRATION 4.7 -- A48 post-boundary deviation)"

#: A24.2 / section 6 line states `build_frames` can commit. An unknown state REFUSES rather
#: than falling through to "not BOTH_ABSENT", which would silently enlarge the M0 risk set.
KNOWN_LINE_STATES = frozenset({"SAME", "TEXT_DIFFERS", "H_ABSENT", "X_ABSENT", "BOTH_ABSENT"})

#: section 6 M7: "emitted headings matching the letter-spaced signature (>= 3 single-character
#: tokens), per architecture". The threshold is the frozen protocol's, not phase 2's exploratory
#: `v08_display_split.py` regex (which required a RUN of >= 4 uppercase single-character tokens
#: on a physical line of ENGINE text). Those differ, and this module follows section 6 because
#: section 6 is the frozen source and A38.1 fixes M7's input as the emitted anchor TEXT. The run
#: length is recorded beside the count as a diagnostic so the stricter reading stays recoverable
#: from the artifact without a second metric existing.
M7_MIN_SINGLE_CHAR_TOKENS = 3

#: Section 5.6's two R1 reliability thresholds, frozen verbatim. Below them, section 5.6 voids
#: the text metrics / the role metric, and Rule 3 (A27.6) owns the decision consequence.
R1_TEXT_THRESHOLD = 0.90
R1_ROLE_THRESHOLD = 0.80
#: R6 is RULED (A41.2): ONE denominator, no candidate set, and no abstention status. The former
#: candidate rules and `AMBIGUOUS_PENDING_A41_RULING` are deleted rather than deprecated, so a
#: consumer cannot read a status the protocol no longer defines.
R1_NOT_EVALUABLE = "NOT_EVALUABLE_NO_R1_PAIRS"

#: A27.5 / section 8.3. One-sided, 95 %.
SECTION8_ALPHA = 0.05
#: Halvings in the Clopper-Pearson bisection. Fixed, so the bound is bit-reproducible; 200 is
#: far past double precision, which is what makes "deterministic" true rather than approximate.
_CP_ITERATIONS = 200


class ScoreInputError(Exception):
    """A result-bearing input is malformed, incomplete or internally inconsistent.

    Deterministic and never a value: a scorer that repaired its input would be deciding what
    the study measured, and the repair would be invisible in the artifact.
    """

    def __init__(self, reason: str, detail=None):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason} {detail!r}")


@dataclass(frozen=True)
class ScoreInputs:
    """Everything the scorer reads, supplied explicitly. No path is discovered here.

    `frames` is the sequence of committed document frames (`build_frames.build_document_frame`
    output). The `frames.json` wrapper belongs to whatever writes it; this module takes the
    document frames themselves so a wrapper change cannot silently reinterpret the population.

    `document_strata` maps document id -> stratum label, read from the committed membership by
    the caller. It feeds section 4.5's "strata filled" count and nothing else.

    THERE IS NO `r1_role_agreement` PARAMETER, and its absence is deliberate. A caller-supplied
    scalar cannot be evidence for a result-bearing reliability gate: it can assert PASS with
    nothing behind it, and nothing downstream could tell. R1 is computed here from the committed
    key and the committed adjudications instead -- see `r1_reliability`.
    """

    frames: tuple[dict, ...]
    oracle_key: dict
    oracle_adjudicated: dict
    cross_engine: dict
    s1: dict
    document_strata: dict = field(default_factory=dict)


# ------------------------------------------------------------------------- small helpers


def _require(record: dict, fields, where: str) -> None:
    missing = [f for f in fields if f not in record]
    if missing:
        raise ScoreInputError(MISSING_REQUIRED_FIELD, {"where": where, "missing": missing})


def rate(numerator: int, denominator: int, *, label: str = "") -> dict:
    """A metric entry: the number, and the denominator it is a fraction of.

    I11 -- a zero CONTENT-BEARING denominator yields VACUOUS, never 0.0 and never 1.0. Both of
    those read as a result; the protocol requires the absence of a population to be visible.
    """
    entry = {"numerator": numerator, "denominator": denominator}
    if label:
        entry["metric"] = label
    if denominator == 0:
        entry["value"] = None
        entry["status"] = VACUOUS
        return entry
    entry["value"] = numerator / denominator
    entry["status"] = "REPORTED"
    return entry


def _key_form(key):
    """One serialized form for an occurrence key. Tuples and lists are the same key.

    A38's own incidental finding: the two sides of the join were compared tuple-vs-list, unequal
    while being the same key, which would have made every matched-heading denominator zero.
    """
    if isinstance(key, (list, tuple)):
        return tuple(_key_form(k) for k in key)
    return key


def _readable(value) -> bool:
    """Is this adjudicated field an answer, or the oracle's own UNREADABLE?"""
    return value is not None and value != _bo().UNREADABLE


# ------------------------------------------------------------- input validation (refusals)


def validate_inputs(inputs: ScoreInputs) -> dict:
    """Refuse malformed, incomplete or self-inconsistent input. Returns what it checked.

    Every check here is a fact about the COMMITTED artifacts, so a failure means the artifact
    chain is wrong rather than that a document scored badly. None of them is a threshold.
    """
    if not isinstance(inputs.frames, (list, tuple)):
        raise ScoreInputError(FRAMES_NOT_A_SEQUENCE, {"type": type(inputs.frames).__name__})

    seen: dict[str, int] = {}
    for frame in inputs.frames:
        _require(
            frame,
            ("document", "document_sha256", "population", "pages", "counts", "architecture_occurrences", "m9"),
            "document frame",
        )
        doc = frame["document"]
        if doc in seen:
            raise ScoreInputError(DUPLICATE_DOCUMENT_IDENTITY, {"document": doc})
        seen[doc] = 1
        if frame["population"] not in BF.KNOWN_POPULATIONS:
            raise ScoreInputError(UNKNOWN_POPULATION, {"document": doc, "population": frame["population"]})
        _validate_frame_internals(frame)
        _validate_occurrences(frame)
        _validate_m9(frame)

    _require(inputs.oracle_key, ("stimuli",), "oracle key")
    # The adjudicated encoding has ONE owner (A38.7). Calling it rather than re-checking the
    # namespaces here means the scorer cannot accept an artifact `build_oracle` would refuse.
    _bo().validate_adjudicated(inputs.oracle_adjudicated, inputs.oracle_key)

    known_docs = set(seen)
    for bid, record in inputs.oracle_key["stimuli"].items():
        if record.get("control_kind") is not None:
            # A40 F5 -- a control has no document frame, and `frames == ()` is what keeps it out
            # of both estimands. If one ever acquired frame membership, skipping it by
            # `control_kind` would silently drop a region that CLAIMS to be in an estimand, so
            # the contradiction refuses here instead of being absorbed.
            if record.get("frames"):
                raise ScoreInputError(
                    CONTROL_STIMULUS_IN_ESTIMAND,
                    {"blind_id": bid, "control_kind": record["control_kind"], "frames": record["frames"]},
                )
            continue
        if record["document"] not in known_docs:
            raise ScoreInputError(STIMULUS_DOCUMENT_NOT_IN_FRAMES, {"blind_id": bid, "document": record["document"]})

    if not isinstance(inputs.s1, dict) or "fires" not in inputs.s1:
        raise ScoreInputError(S1_ARTIFACT_MISSING, {"got": sorted(inputs.s1) if isinstance(inputs.s1, dict) else None})
    # A47.6 adds `confirmatory_status` to the REQUIRED fields, not the optional ones. The
    # cross-engine artifact is the head of the A45-affected path, so an artifact that cannot
    # say what its section 4.7 status is must not be scorable at all -- a `.get()` default
    # here would let a provenance-less artifact through as though it were confirmatory.
    _require(inputs.cross_engine, ("per_document", "confirmatory_status"), "cross-engine control")
    # ...and the VALUE must be the required one, not merely present. Presence alone let a
    # cross-engine artifact carrying a fabricated "CONFIRMATORY" score cleanly, because every
    # layer read the status from the same authority that supplied it. The expected value is a
    # constant HERE, deliberately independent of the artifact and of the continuation record:
    # the scorer's frozen consumer allowlist forbids importing the provenance module, and an
    # expectation imported from the thing under test would not be an expectation at all.
    # `x30` asserts this constant still equals `continuation_provenance.NON_CONFIRMATORY`, so
    # the two cannot drift apart silently.
    # A48 -- THE KEY MAY NOT SELF-CERTIFY ITS A27.3 STATE.
    #
    # `d_frame_census` and `d_decision_route_required` are result-bearing: they decide whether
    # the complete-census human decision evidence is required at all. Trusting them because the
    # builder wrote them would let a key claim `61` over a real 60-region census, consistently
    # shorten its own route metadata, and score with Rule 1's evidence simply absent -- and a
    # true census of 60 is exactly the case where Rule 1 MAY select X.
    #
    # So both are re-derived here from facts the key does not own: the census by summing each
    # COMMITTED frame's producer-declared count, and the predicate by calling the frozen owner.
    # D membership is NOT re-derived from region contents; the committed counts remain the
    # producer's census, which is the same quantity `decide_architecture` reads.
    key = inputs.oracle_key
    if "d_decision_route_required" in key or "d_frame_census" in key:
        _require(key, ("d_frame_census", "d_decision_route_required"), "oracle key (A48)")
        census = 0
        for frame in inputs.frames:
            counts = frame.get("counts") or {}
            if "d_frame_census" not in counts:
                raise ScoreInputError(D_CENSUS_MISSING, {"document": frame.get("document")})
            census += int(counts["d_frame_census"])
        if int(key["d_frame_census"]) != census:
            raise ScoreInputError(
                D_CENSUS_MISMATCH, {"key": key["d_frame_census"], "committed_frames": census}
            )
        want = MC.d_decision_route_required(census)
        if bool(key["d_decision_route_required"]) != want:
            raise ScoreInputError(
                D_BUDGET_CLAIM_MISMATCH,
                {"key_claims": key["d_decision_route_required"], "frozen_predicate": want, "census": census},
            )

    got = inputs.cross_engine["confirmatory_status"]
    if got != REQUIRED_CONFIRMATORY_STATUS:
        raise ScoreInputError(
            WRONG_CONFIRMATORY_STATUS, {"got": got, "required": REQUIRED_CONFIRMATORY_STATUS}
        )
    # EXACT SET EQUALITY, not containment, and it is checked HERE -- before `qualification` runs.
    #
    # I13's consequence is a COUNT over documents ("failure on more than a third of sampled
    # documents applies the qualification to BOTH headlines"), so the artifact's document set is a
    # DENOMINATOR. Containment alone lets an extra row move that denominator: one extra passing
    # row dilutes the fraction and can withhold a qualification that was earned, one extra failing
    # row can manufacture one, and a duplicated document with conflicting verdicts makes the
    # result depend on row order. None of those is detectable downstream, because the qualification
    # is reported as a property of the run rather than of a population a reader can re-derive.
    rows = inputs.cross_engine["per_document"]
    for index, row in enumerate(rows):
        # STRUCTURAL, before anything reads a verdict. A row missing `passed` would otherwise reach
        # `qualification`, where `not row["passed"]` on an absent key raises far from the cause --
        # or, worse, a `.get` default would silently invent a verdict for a document.
        missing = [f for f in ("document", "passed") if f not in row]
        if missing:
            raise ScoreInputError(
                CROSS_ENGINE_ROW_MALFORMED, {"index": index, "missing": missing, "row_keys": sorted(row)}
            )
    documents = [row["document"] for row in rows]
    duplicates = sorted({d for d in documents if documents.count(d) > 1})
    if duplicates:
        raise ScoreInputError(CROSS_ENGINE_DUPLICATE_DOCUMENT, {"documents": duplicates[:4], "n_rows": len(rows)})
    extra = sorted(set(documents) - known_docs)
    if extra:
        raise ScoreInputError(CROSS_ENGINE_EXTRA_DOCUMENT, {"documents": extra[:4], "scored": sorted(known_docs)})
    for doc in sorted(known_docs):
        if doc not in set(documents):
            raise ScoreInputError(CROSS_ENGINE_DOCUMENT_MISSING, {"document": doc})
        # UNCONDITIONAL. The old `if inputs.document_strata and ...` guard meant an EMPTY mapping
        # bypassed the check entirely, and section 4.5 then counted zero strata filled -- turning
        # missing input into an apparently real INADEQUATE verdict about the holdout. A population
        # fact the caller failed to supply is not a finding about the population.
        if doc not in inputs.document_strata:
            raise ScoreInputError(STRATUM_MISSING, {"document": doc, "n_supplied": len(inputs.document_strata)})
    return {
        "n_documents": len(known_docs),
        "documents": sorted(known_docs),
        "cross_engine_population": "exact set equality with the scored frames; no extras, no duplicates",
    }


def _validate_frame_internals(frame: dict) -> None:
    """The committed line/region predicates must agree with the frozen rules that made them.

    This is the one place the scorer recomputes something: `build_frames` commits BOTH the
    comparable quantities (texts, signatures) and the booleans derived from them, and I9
    requires M0's eligibility and the D-frame's to be ONE set. A frame whose committed boolean
    disagrees with `neutral_identity`'s own predicate is not a frame this module can score.
    """
    doc = frame["document"]
    for page in frame["pages"]:
        _require(page, ("page_number", "neutral_lines", "regions"), f"page frame in {doc}")
        state_by_key = {}
        for line in page["neutral_lines"]:
            _require(line, ("key", "line_state", "in_m0_risk_set"), f"neutral line in {doc}")
            st = line["line_state"]
            _require(
                st,
                ("state", "h_text", "x_text", "h_signature", "x_signature", "diagnostics"),
                f"line_state in {doc}",
            )
            if st["state"] not in KNOWN_LINE_STATES:
                raise ScoreInputError(UNKNOWN_LINE_STATE, {"document": doc, "line": line["key"], "state": st["state"]})
            recomputed = {
                "text_discordance": NI.text_discordance(st),
                "segmentation_discordance": NI.segmentation_discordance(st),
                "in_m0_risk_set": NI.in_risk_set(st),
            }
            committed = {
                "text_discordance": st.get("text_discordance"),
                "segmentation_discordance": st.get("segmentation_discordance"),
                "in_m0_risk_set": line["in_m0_risk_set"],
            }
            if committed != recomputed:
                raise ScoreInputError(
                    LINE_STATE_PREDICATE_DRIFT,
                    {"document": doc, "line": line["key"], "committed": committed, "recomputed": recomputed},
                )
            state_by_key[tuple(line["key"])] = st

        for region in page["regions"]:
            _require(
                region,
                ("region_ordinal", "neutral_line_keys", "d_frame", "d_reasons", "anchor_evidence"),
                f"region in {doc}",
            )
            # THE EXACT PRODUCER INVARIANT: `build_frames` writes `d_frame = bool(reasons)` from the
            # same expression that builds `d_reasons`. The per-predicate checks below each inspect
            # ONE predicate, so they cannot see the flag and the reason list disagreeing as a whole
            # -- e.g. a concordant region carrying a reason with `d_frame` false satisfies every one
            # of them.
            if bool(region["d_frame"]) != bool(region["d_reasons"]):
                raise ScoreInputError(
                    D_FRAME_FLAG_DRIFT,
                    {
                        "document": doc,
                        "region": region["region_ordinal"],
                        "d_frame": region["d_frame"],
                        "d_reasons": region["d_reasons"],
                    },
                )
            evidence = region["anchor_evidence"]
            differs = set(map(tuple, evidence["H"])) != set(map(tuple, evidence["X"]))
            if bool(evidence["differ"]) != differs or (BF.ANCHOR_DISCORDANCE in region["d_reasons"]) != differs:
                raise ScoreInputError(
                    ANCHOR_EVIDENCE_DRIFT,
                    {"document": doc, "region": region["region_ordinal"], "differ": evidence["differ"]},
                )
            states = [state_by_key[tuple(k)] for k in region["neutral_line_keys"] if tuple(k) in state_by_key]
            if len(states) != len(region["neutral_line_keys"]):
                raise ScoreInputError(
                    MISSING_REQUIRED_FIELD,
                    {"where": f"region {region['region_ordinal']} of {doc}", "missing": ["neutral line"]},
                )
            # I9 -- the same predicates decide M0 and D-frame membership. A discordant line in a
            # region the frame calls concordant would mean two eligibility sets, and the D-frame
            # census (Rule 1's population) and M0 would be measuring different things.
            line_level = any(NI.line_discordance(s) for s in states)
            if line_level and not region["d_frame"]:
                raise ScoreInputError(
                    D_FRAME_ELIGIBILITY_DRIFT,
                    {"document": doc, "region": region["region_ordinal"], "reason": "discordant line outside D-frame"},
                )
            if region["d_frame"] and not (line_level or differs):
                raise ScoreInputError(
                    D_FRAME_ELIGIBILITY_DRIFT,
                    {"document": doc, "region": region["region_ordinal"], "reason": "D-frame with no predicate"},
                )


def _validate_occurrences(frame: dict) -> None:
    """Every emitted occurrence is MATCHABLE or UNMATCHED, and keys are unique per arm."""
    doc = frame["document"]
    for arm in ARMS:
        rows = frame["architecture_occurrences"].get(arm)
        if rows is None:
            raise ScoreInputError(MISSING_REQUIRED_FIELD, {"where": f"architecture_occurrences of {doc}", "arm": arm})
        keys = []
        for row in rows:
            _require(
                row,
                ("anchor", "page_number", "region_ordinal", "occurrence_key", "match_status", "immediate_parent"),
                f"occurrence in {doc}/{arm}",
            )
            _require(row["anchor"], ("page_number", "line_number", "kind", "text"), f"anchor in {doc}/{arm}")
            status, key = row["match_status"], row["occurrence_key"]
            if status not in ("MATCHABLE", "UNMATCHED") or (status == "MATCHABLE") != (key is not None):
                raise ScoreInputError(
                    OCCURRENCE_RECORD_INCOMPLETE,
                    {"document": doc, "arm": arm, "match_status": status, "has_key": key is not None},
                )
            if key is not None:
                keys.append(_key_form(key))
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        if duplicates:
            raise ScoreInputError(DUPLICATE_OCCURRENCE_KEY, {"document": doc, "arm": arm, "keys": duplicates[:4]})


def _validate_m9(frame: dict) -> None:
    """The M9 basis must be complete, and the coverage floor must still be production's 0.85.

    A committed floor other than 0.85 is not a document that scored badly; it is a threshold
    that moved, and section 4.7 makes that a deviation rather than a result.
    """
    for arm in ARMS:
        facts = frame["m9"].get(arm)
        if facts is None:
            raise ScoreInputError(MISSING_REQUIRED_FIELD, {"where": f"m9 of {frame['document']}", "arm": arm})
        _require(
            facts,
            (
                "derive_size_bands_returns_a_band",
                "coverage",
                "coverage_floor",
                "coverage_meets_floor",
                "n_lines_total",
                "n_margin_numbered_lines",
            ),
            f"m9 {arm} of {frame['document']}",
        )
        if facts["coverage_floor"] != 0.85:
            raise ScoreInputError(
                COVERAGE_FLOOR_DRIFT,
                {"document": frame["document"], "arm": arm, "coverage_floor": facts["coverage_floor"]},
            )
        if facts["coverage_meets_floor"] != (facts["coverage"] >= facts["coverage_floor"]):
            raise ScoreInputError(
                COVERAGE_FLOOR_DRIFT,
                {"document": frame["document"], "arm": arm, "reason": "committed verdict disagrees with the floor"},
            )


# ------------------------------------------------------------------------------ the M0 block


def m0_block(frame: dict) -> dict:
    """M0a / M0b / M0-any / M0b_defined / both_absent, and M0c beside them but never pooled.

    The line rates come from `neutral_identity.m0`, which owns A23's risk set: neutral lines
    emitted by AT LEAST ONE arm. A `BOTH_ABSENT` line is reported as a raw count and appears in
    no denominator -- counting it as agreement would make the rate a function of how much page
    furniture GPO set, which is a property of the layout and not of the seam.

    M0c is REGION-level (anchor sets differ) and is emitted alongside, never summed or averaged
    into the line rates: section 5's "never pooled with the above" is explicit.
    """
    states = [line["line_state"] for page in frame["pages"] for line in page["neutral_lines"]]
    block = dict(NI.m0(states))
    regions = [r for page in frame["pages"] for r in page["regions"]]
    anchor_discordant = [r for r in regions if BF.ANCHOR_DISCORDANCE in r["d_reasons"]]

    block["M0a_text_rate"] = rate(block["M0a_text"], block["risk_set"], label="M0a")
    block["M0b_segmentation_rate"] = rate(block["M0b_segmentation"], block["risk_set"], label="M0b_on_risk_set")
    block["M0_any_rate"] = rate(block["M0_any"], block["risk_set"], label="M0-any")
    # A23's reporting rule: only the rate on the DEFINED population may be described as "the
    # fraction of comparable groupings that disagree", and both must be emitted.
    block["M0b_rate_on_defined"] = rate(block["M0b_segmentation"], block["M0b_defined"], label="M0b_on_defined")
    block["M0c_anchor_regions"] = len(anchor_discordant)
    block["M0c_rate"] = rate(len(anchor_discordant), len(regions), label="M0c")
    block["M0c_pooling"] = "REGION-level; never pooled with the line rates (section 5)"
    block["both_absent_in_any_denominator"] = False
    block["risk_set_definition"] = "neutral lines with state != BOTH_ABSENT (A23, I3)"
    return block


# -------------------------------------------------------------------------------- M7


def m7_signature(text: str) -> dict:
    """The letter-spaced display-split signature for ONE emitted heading text.

    A self-signature over each architecture's OWN output (red-team #3): no oracle, and
    explicitly not a correctness measure. Tokenised on the section 6.2 normalisation so an
    NFKC-equivalent separator cannot hide a split.
    """
    normalized = m2_normalize(text or "")
    tokens = normalized.split(" ") if normalized else []
    singles = [t for t in tokens if len(t) == 1]
    run, longest = 0, 0
    for token in tokens:
        run = run + 1 if len(token) == 1 else 0
        longest = max(longest, run)
    return {
        "normalized": normalized,
        "n_tokens": len(tokens),
        "n_single_char_tokens": len(singles),
        "longest_single_char_run": longest,  # DIAGNOSTIC ONLY -- see M7_MIN_SINGLE_CHAR_TOKENS
        "matches": len(singles) >= M7_MIN_SINGLE_CHAR_TOKENS,
    }


def m7_block(frame: dict) -> dict:
    """M7 per architecture over the document's COMPLETE emitted occurrence census.

    Fires on 100 % of the holdout (section 6), so the population is every emitted occurrence --
    not the adjudicated subset, and not a frame. The matching texts are printed, not only
    counted: a count cannot distinguish a real signature from a coincidence.
    """
    out = {"threshold_single_char_tokens": M7_MIN_SINGLE_CHAR_TOKENS}
    for arm in ARMS:
        rows = frame["architecture_occurrences"][arm]
        hits = [(r, m7_signature(r["anchor"]["text"])) for r in rows]
        matched = [(r, s) for r, s in hits if s["matches"]]
        out[arm] = {
            "n_emitted": len(rows),
            "n_display_split": len(matched),
            "rate": rate(len(matched), len(rows), label="M7"),
            "instances": [
                {
                    "page_number": r["anchor"]["page_number"],
                    "line_number": r["anchor"]["line_number"],
                    "text": r["anchor"]["text"],
                    "n_single_char_tokens": s["n_single_char_tokens"],
                    "longest_single_char_run": s["longest_single_char_run"],
                }
                for r, s in matched
            ],
        }
    return out


# ------------------------------------------------------------------- M9 / Rule 0 raw facts


def _clause_loss(h_ok: bool, x_ok: bool) -> dict:
    """Which arm, if either, LOSES a boolean Rule 0 clause the other keeps.

    Shaped exactly like `methodology_contracts.margin_line_loss` so the three clauses read the
    same way. BOTH losing is not a loss here: section 7.2 rule 0 fires only when EXACTLY one
    architecture loses, and a shared failure is neutral for RQ1 and a FAILURE retained in RQ2.
    """
    if h_ok and not x_ok:
        return {"loser": "X", "fires": True, "h": h_ok, "x": x_ok}
    if x_ok and not h_ok:
        return {"loser": "H", "fires": True, "h": h_ok, "x": x_ok}
    return {"loser": None, "fires": False, "h": h_ok, "x": x_ok}


def rule0_facts(frame: dict) -> dict:
    """M9's raw facts per arm, and the three per-clause asymmetries. NO outcome is decided.

    I12 says M9 can reject an arm outright before any other metric is consulted. The REJECTION
    is section 7.2's; this reports which arm lost which clause, with no tolerance anywhere: the
    margin-line clause is `margin_line_loss` verbatim (A39.1 -- any strictly positive deficit
    fires), and the coverage clause is the committed 0.85 floor, already asserted unmoved.
    """
    h, x = frame["m9"]["H"], frame["m9"]["X"]
    return {
        "document": frame["document"],
        "population": frame["population"],
        "H": {k: v for k, v in h.items() if k != "margin_numbered_line_keys"},
        "X": {k: v for k, v in x.items() if k != "margin_numbered_line_keys"},
        "coverage_floor": h["coverage_floor"],
        "band_loss": _clause_loss(h["derive_size_bands_returns_a_band"], x["derive_size_bands_returns_a_band"]),
        "coverage_loss": _clause_loss(h["coverage_meets_floor"], x["coverage_meets_floor"]),
        "margin_line_loss": MC.margin_line_loss(h["n_margin_numbered_lines"], x["n_margin_numbered_lines"]),
        "margin_line_quantity": "count of Page.lines where line_number is not None (A39.1); no tolerance",
        "rule0_outcome": None,
        "rule0_owner": "decide_architecture -- A27.4's outcome enum is deliberately absent here",
    }


# ----------------------------------------------------------------- the M1-M5 heading join


def _adjudicated_occurrences(record: dict, answer: dict) -> list[dict]:
    """Every adjudicated heading of one stimulus, resolved to an A30 occurrence key.

    The resolution is `build_oracle.resolve_adjudicated_occurrence` (A38.7): committed facts
    only, no tolerance, no text/kind/order fallback, a tie or an absent candidate -> UNMATCHED.
    A refusal is CARRIED, never dropped and never converted into a match.
    """
    out = []
    for ordinal, heading in enumerate(answer.get("headings", [])):
        resolved = _bo().resolve_adjudicated_occurrence(record, heading)
        out.append(
            {
                "ordinal": ordinal,
                "heading": heading,
                "resolved": resolved["matched"],
                "occurrence_key": _key_form(resolved["occurrence_key"]) if resolved["matched"] else None,
                "refusal": None if resolved["matched"] else resolved.get("reason"),
            }
        )
    return out


def _parent_answer_kind(heading: dict, answer: dict, item: dict) -> str:
    """Which of the FOUR frozen `parent` answers this is: NONE / OFF_REGION / UNREADABLE / TEXT.

    WHY THIS EXISTS, and what it repairs. The adjudicator writes literal `NONE` and `OFF_REGION`
    (PRE-REGISTRATION section 5.3; `adjudicator_prompt.md` section 3). Comparing those as parent
    TEXT is wrong in both directions: `"NONE"` against an emitted `None` scores a correct root
    heading as WRONG, and `"OFF_REGION"` can never equal any emitted parent, so every off-region
    answer would count against an architecture for a limit of the ORACLE's field of view. Reading
    a JSON null as "no parent" hid this, because a fixture could satisfy M4 without ever producing
    the representation the adjudicator is actually asked for.

    OFF_REGION LEAVES THE POPULATION, and that is a reading, not an omission. Section 6 fires M4
    on "matched headings whose parent is in-region **or resolvable**", and **no frozen source
    defines a resolver**: `OFF_REGION` appears exactly twice in the whole study -- section 5.3 and
    the prompt -- and neither `build_oracle` nor `methodology_contracts` carries one. Inventing a
    resolver here would have to recover the parent from an architecture's own document-scope
    hierarchy, which is the very quantity M4 measures. So it is excluded and COUNTED, never
    silently scored and never charged to an arm.

    A null REFUSES. It is not one of the four answers, and the frozen encoding has a spelling for
    every case the adjudicator can face.
    """
    parent = heading.get("parent")
    if parent is None:
        raise ScoreInputError(
            PARENT_MISSING,
            {
                "blind_id": answer.get("id"),
                "ordinal": item["ordinal"],
                "expected": ["<printed text>", "NONE", "OFF_REGION", _bo().UNREADABLE],
            },
        )
    if parent == _bo().UNREADABLE:
        return "UNREADABLE"
    if parent == ORACLE_PARENT_OFF_REGION:
        return ORACLE_PARENT_OFF_REGION
    if parent == ORACLE_PARENT_NONE:
        return ORACLE_PARENT_NONE
    return "TEXT"


def _blank_heading_counts() -> dict:
    return {
        "n_adjudicated": 0,
        "n_adjudicated_unresolvable": 0,
        "n_emitted": {arm: 0 for arm in ARMS},
        "n_matched": {arm: 0 for arm in ARMS},
        "m2_exact": {arm: 0 for arm in ARMS},
        "m2_scored": {arm: 0 for arm in ARMS},
        "m2_excluded_unreadable": 0,
        "m3_clean": {arm: 0 for arm in ARMS},
        "m3_scored": {arm: 0 for arm in ARMS},
        "m3_boundary": {arm: {"WELD": 0, "SPLIT": 0, "OK": 0, "TEXT_ERROR": 0} for arm in ARMS},
        "m3_outcomes": {outcome.value: 0 for outcome in M3.HeadingOutcome},
        "m3_excluded_no_reference": 0,
        "m4_correct": {arm: 0 for arm in ARMS},
        "m4_scored": {arm: 0 for arm in ARMS},
        "m4_excluded_unreadable": 0,
        # A parent the adjudicator could read but could not SEE. Reported, never scored.
        "m4_excluded_off_region": 0,
        # RULE 1's FOURTH CONDITION, as a PAIRED quantity (A42.3). A5 row 4 is a per-heading
        # existential -- "no heading whose immediate parent is correct under H and wrong under X" --
        # and the per-arm counts above CANNOT express it: two headings, one wrong under each arm,
        # leave `m4_correct` equal on both while the condition is violated. The pair is counted
        # here, where the two arms' per-heading results are both in hand; nothing downstream can
        # recover it from the aggregates.
        "m4_h_correct_x_wrong": 0,
        # The mirror is a DIAGNOSTIC. Rule 1 is one-directional and reads only the line above; this
        # is emitted so a reader can see the pairing is real and not an artefact of one arm.
        "m4_x_correct_h_wrong": 0,
        # The occurrence keys of the vetoing headings. A count cannot be checked against the
        # adjudication; the instances can, and a veto that decides an architecture should name the
        # headings it fired on.
        "m4_h_correct_x_wrong_keys": [],
        "m5_agree": {arm: 0 for arm in ARMS},
        "m5_scored": {arm: 0 for arm in ARMS},
        "m5_excluded_unscorable": {arm: 0 for arm in ARMS},
        "m5_excluded_unreadable": 0,
        "n_stimuli": 0,
    }


def _score_stimulus(counts: dict, record: dict, answer: dict) -> None:
    """Fold one (stimulus, route) into the running heading counts for its frame.

    THE POPULATIONS, each as section 6 fixes it:
        M1 recall     denominator = the ADJUDICATED enumeration (I10), never the emitted one
        M1 precision  denominator = the arm's EMITTED occurrences for this region
        M2/M4/M5      matched headings, minus the oracle's own UNREADABLE fields
        M3            adjudicated headings with a readable reference; an arm that emitted
                      nothing scores as a maximal TEXT_ERROR and STAYS in the denominator,
                      because `m3_boundaries` says a severe failure may never become an
                      exclusion (A9)
    """
    counts["n_stimuli"] += 1
    adjudicated = _adjudicated_occurrences(record, answer)
    counts["n_adjudicated"] += len(adjudicated)
    counts["n_adjudicated_unresolvable"] += sum(1 for a in adjudicated if not a["resolved"])

    emitted_by_key = {}
    for arm in ARMS:
        rows = record["architecture_occurrences"][arm]
        counts["n_emitted"][arm] += len(rows)
        emitted_by_key[arm] = {_key_form(r["occurrence_key"]): r for r in rows if r["occurrence_key"] is not None}

    for item in adjudicated:
        heading = item["heading"]
        oracle_text = heading.get("text")
        matched = {arm: emitted_by_key[arm].get(item["occurrence_key"]) if item["resolved"] else None for arm in ARMS}
        for arm in ARMS:
            if matched[arm] is not None:
                counts["n_matched"][arm] += 1

        # ---- M3, on projected TEXT plus the oracle. A segmentation label may never reach it.
        if not item["resolved"]:
            pass  # identity unknown: scoring it would fabricate a defect for both arms
        elif not _readable(oracle_text):
            counts["m3_excluded_no_reference"] += 1
        else:
            h_text = matched["H"]["anchor"]["text"] if matched["H"] else ""
            x_text = matched["X"]["anchor"]["text"] if matched["X"] else ""
            outcome, h_score, x_score = M3.heading_outcome(oracle_text, h_text, x_text)
            counts["m3_outcomes"][outcome.value] += 1
            for arm, score in (("H", h_score), ("X", x_score)):
                counts["m3_scored"][arm] += 1
                counts["m3_clean"][arm] += int(score.clean)
                counts["m3_boundary"][arm]["WELD"] += score.weld
                counts["m3_boundary"][arm]["SPLIT"] += score.split
                counts["m3_boundary"][arm]["OK"] += score.ok
                counts["m3_boundary"][arm]["TEXT_ERROR"] += score.text_error

        # ---- M2 / M4 / M5, on matched headings only
        parent_kind = _parent_answer_kind(heading, answer, item)
        if not _readable(oracle_text):
            counts["m2_excluded_unreadable"] += 1
        if parent_kind == "UNREADABLE":
            counts["m4_excluded_unreadable"] += 1
        elif parent_kind == ORACLE_PARENT_OFF_REGION:
            counts["m4_excluded_off_region"] += 1
        if heading.get("role") == _bo().UNREADABLE:
            counts["m5_excluded_unreadable"] += 1

        # Each arm's M4 result for THIS heading: True, False, or None when the heading is not in
        # M4's content-bearing population for that arm. Held per heading so the pair can be
        # compared once both arms are known -- see the veto below.
        m4_result = {arm: None for arm in ARMS}

        for arm in ARMS:
            occurrence = matched[arm]
            if occurrence is None:
                continue
            if _readable(oracle_text):
                counts["m2_scored"][arm] += 1
                counts["m2_exact"][arm] += int(m2_normalize(oracle_text) == m2_normalize(occurrence["anchor"]["text"]))

            # M4 -- the IMMEDIATE parent only. Full ancestry would score a different quantity
            # and would hide a one-level hierarchy error inside a correct grandparent.
            #
            # The adjudicated field is one of FOUR answers, never a free string (section 5.3):
            # printed parent text, `NONE`, `OFF_REGION`, or `UNREADABLE`. Only the first two are
            # in M4's content-bearing population -- see `_parent_answer_kind`.
            emitted_parent = occurrence["immediate_parent"]
            if parent_kind == ORACLE_PARENT_NONE:
                counts["m4_scored"][arm] += 1
                m4_result[arm] = emitted_parent is None
                counts["m4_correct"][arm] += int(m4_result[arm])
            elif parent_kind == "TEXT":
                counts["m4_scored"][arm] += 1
                m4_result[arm] = emitted_parent is not None and m2_normalize(heading["parent"]) == m2_normalize(
                    emitted_parent
                )
                counts["m4_correct"][arm] += int(m4_result[arm])

            role = heading.get("role")
            if role == _bo().UNREADABLE:
                continue
            if role is None:
                raise ScoreInputError(ROLE_MISSING, {"blind_id": answer.get("id"), "ordinal": item["ordinal"]})
            agreement = MC.m5_agreement(role, occurrence["anchor"]["kind"])
            if agreement is None:
                counts["m5_excluded_unscorable"][arm] += 1  # A36.7 -- out of the DENOMINATOR
            else:
                counts["m5_scored"][arm] += 1
                counts["m5_agree"][arm] += int(agreement)

        # ---- Rule 1's fourth condition, PAIRED (A5 row 4 / A20, ruled by A42.3)
        #
        # THE POPULATION IS HEADINGS SCORED UNDER M4 FOR BOTH ARMS, and that is a reading with a
        # reason. A heading X never emitted is not in M4's population for X -- section 6 fires M4
        # on MATCHED headings -- so charging it here would count one failure twice, in M1's recall
        # and again as a hierarchy regression. Nothing escapes by it: an unemitted heading scores a
        # maximal TEXT_ERROR in M3 (A9), so if H is clean it is already `X_REGRESSES`, and Rule 1's
        # condition 2 vetoes at ZERO. The veto below is therefore about hierarchy specifically,
        # which is the quantity A5 row 4 names.
        if m4_result["H"] is True and m4_result["X"] is False:
            counts["m4_h_correct_x_wrong"] += 1
            counts["m4_h_correct_x_wrong_keys"].append(_key_form(item["occurrence_key"]))
        if m4_result["X"] is True and m4_result["H"] is False:
            counts["m4_x_correct_h_wrong"] += 1


def _heading_metrics_from_counts(counts: dict, r1: dict) -> dict:
    """Turn the raw heading counts into the reported metric entries."""
    resolvable = counts["n_adjudicated"] - counts["n_adjudicated_unresolvable"]
    metrics = {
        "counts": counts,
        "M1": {
            arm: {
                # I10 -- the ADJUDICATED enumeration, including headings whose geometric
                # identity refused: they were printed, and removing them would shrink the
                # denominator invisibly. The sensitivity figure below is explicitly secondary.
                "recall": rate(counts["n_matched"][arm], counts["n_adjudicated"], label="M1 recall"),
                "precision": rate(counts["n_matched"][arm], counts["n_emitted"][arm], label="M1 precision"),
                "recall_sensitivity_excluding_unresolvable": rate(
                    counts["n_matched"][arm], resolvable, label="M1 recall (SECONDARY)"
                ),
            }
            for arm in ARMS
        },
        "M2": {arm: rate(counts["m2_exact"][arm], counts["m2_scored"][arm], label="M2") for arm in ARMS},
        "M3": {
            "clean_rate": {arm: rate(counts["m3_clean"][arm], counts["m3_scored"][arm], label="M3") for arm in ARMS},
            "boundary_outcomes": counts["m3_boundary"],
            "heading_outcomes": counts["m3_outcomes"],
            "decision_unit": "heading occurrence (A3); WELD/SPLIT tallies never substitute for it",
            "consumes": "projected text + oracle only -- never a segmentation label (section 5)",
        },
        "M4": {arm: rate(counts["m4_correct"][arm], counts["m4_scored"][arm], label="M4") for arm in ARMS},
        "M5": {
            arm: {
                "agreement": rate(counts["m5_agree"][arm], counts["m5_scored"][arm], label="M5"),
                "excluded_unscorable": counts["m5_excluded_unscorable"][arm],
            }
            for arm in ARMS
        },
    }
    metrics["M4"] = {
        **metrics["M4"],
        "scope": "immediate parent only (A38.4 penultimate breadcrumb element)",
        "excluded_unreadable": counts["m4_excluded_unreadable"],
        # Reported beside the rate, not folded into it: section 6's population is "in-region or
        # resolvable", and no frozen source defines a resolver for an off-region parent.
        "excluded_off_region": counts["m4_excluded_off_region"],
        "parent_answers": f"printed text | {ORACLE_PARENT_NONE} | {ORACLE_PARENT_OFF_REGION} | {_bo().UNREADABLE}",
        # A5 row 4's PAIRED quantity, surfaced beside the rates rather than left in `counts`. This
        # is the fact `decide_architecture` reads for Rule 1's fourth condition; the rates above
        # cannot express it and must not be substituted for it (A42.3).
        "h_correct_x_wrong": counts["m4_h_correct_x_wrong"],
        "h_correct_x_wrong_occurrences": counts["m4_h_correct_x_wrong_keys"],
        "x_correct_h_wrong": counts["m4_x_correct_h_wrong"],
        "paired_population": "headings scored under M4 for BOTH arms; an unmatched heading is M1 "
        "recall's miss and M3's TEXT_ERROR, never charged twice here",
        "veto_owner": "decide_architecture -- Rule 1 condition 4; no consequence is applied here",
    }
    metrics["M5"]["r1_role_gate"] = _r1_role_gate(r1)
    metrics["M5"]["licenses"] = "corroboration only -- section 6 forbids M5 deciding anything"
    # Section 5.6's other consequence: below 0.90 the TEXT metrics are void. Reported beside M2
    # and M3 rather than applied, because Rule 3 (A27.6) owns the consequence.
    metrics["r1_text_gate"] = {
        "status": r1["text"]["status"],
        "threshold": R1_TEXT_THRESHOLD,
        "observed_by_route": r1["text"]["by_route"],
        "voids_text_metrics_if_FAIL": ["M2", "M3"],
        "owner": "Rule 3 gate vector (A27.6); this module reports the FACTS only",
    }
    return metrics


def _r1_pair_facts(key: dict, adjudicated: dict, primary_bid: str, repeat_bid: str, route: str) -> dict:
    """One (primary, repeat) answer pair on ONE route: the per-heading agreement facts.

    PAIRING IS BY RESOLVED A30 OCCURRENCE KEY, and that is the least-invented option rather than a
    free choice: A30.3 requires the repeat to resolve its OWN `start_x_px` independently, the A30
    occurrence key is the study's only cross-answer heading identity, and A30.3 already speaks of
    an `R1_start_identity_agreement` over repeated items -- which presupposes comparing the two
    resolutions. Pairing by list position would instead assume the two enumerations are aligned,
    which is exactly the reliability question R1 asks.

    ROLE AGREEMENT USES THE FINE section 5.3 ROLE, not the M5 coarsening: A36.7 says "the
    adjudicator continues to record the fine section 5.3 role. **M5 alone** coarsens it", so
    coarsening here would make R1 agree wherever two different fine roles collapse to the same
    M5 class -- a reliability measure that hides the disagreement it exists to find.
    """
    p_rows = _adjudicated_occurrences(key["stimuli"][primary_bid], adjudicated[route][primary_bid])
    r_rows = _adjudicated_occurrences(key["stimuli"][repeat_bid], adjudicated[route][repeat_bid])

    # ONE-TO-ONE ON UNIQUELY RESOLVED KEYS (R6.1). A key resolved more than once on either side is
    # NOT pairable: collapsing the rows through a dict would silently keep the last one, and
    # choosing among duplicates would invent a pairing. Those rows stay denominator-bearing.
    p_counts = Counter(r["occurrence_key"] for r in p_rows if r["resolved"])
    r_counts = Counter(r["occurrence_key"] for r in r_rows if r["resolved"])
    p_unique = {k for k, n in p_counts.items() if n == 1}
    r_unique = {k for k, n in r_counts.items() if n == 1}
    pairable = sorted(p_unique & r_unique, key=lambda k: MC.canonical(list(k)))

    p_by_key = {r["occurrence_key"]: r["heading"] for r in p_rows if r["resolved"] and r["occurrence_key"] in p_unique}
    r_by_key = {r["occurrence_key"]: r["heading"] for r in r_rows if r["resolved"] and r["occurrence_key"] in r_unique}

    # R6.2 -- EXACT equality of the values as returned. No `m2_normalize`, no NFKC, no whitespace
    # collapse, no case folding: section 5.3 asks for exact transcription with case and internal
    # spacing preserved, and M2's normalisation would hide precisely the spacing instability the
    # repeat exists to detect. UNREADABLE is denominator-bearing and earns no numerator.
    text_agree = sum(
        1
        for k in pairable
        if _readable(p_by_key[k].get("text"))
        and _readable(r_by_key[k].get("text"))
        and p_by_key[k]["text"] == r_by_key[k]["text"]
    )
    # R6.3 -- the exact FINE section 5.3 role (A36.7: "M5 alone coarsens it"). UNREADABLE never
    # agrees, including against itself: two illegible answers are an absence of evidence, and
    # counting them as reliability would let repeated unreadability manufacture a PASS.
    role_agree = sum(
        1
        for k in pairable
        if _readable(p_by_key[k].get("role"))
        and _readable(r_by_key[k].get("role"))
        and p_by_key[k]["role"] == r_by_key[k]["role"]
    )

    matched = len(pairable)
    # THE SYMMETRIC UNION over COMPLETE enumerations: every matched pair once, plus every
    # occurrence on either side that did not pair -- unresolved, one-sided, or non-uniquely
    # resolved. It is |P| + |R| - matched, never |set(resolved keys)|.
    denominator = len(p_rows) + len(r_rows) - matched
    return {
        "route": route,
        "primary_blind_id": primary_bid,
        "repeat_blind_id": repeat_bid,
        "n_primary_headings": len(p_rows),
        "n_repeat_headings": len(r_rows),
        "n_primary_unresolved": sum(1 for r in p_rows if not r["resolved"]),
        "n_repeat_unresolved": sum(1 for r in r_rows if not r["resolved"]),
        "n_primary_non_unique": sum(n for n in p_counts.values() if n > 1),
        "n_repeat_non_unique": sum(n for n in r_counts.values() if n > 1),
        "n_matched_pairs": matched,
        "n_text_agree": text_agree,
        "n_role_agree": role_agree,
        "denominator": denominator,
    }


def _r1_status(numerator: int, denominator: int, threshold: float) -> dict:
    """R6.4 -- the heading-occurrence MICRO-AVERAGE within one route, against a frozen threshold.

    Numerator and denominator are summed over heading occurrences across the route's R1 pairs, not
    averaged over per-region rates: a region with one heading and a region with forty are not equal
    evidence about an adjudicator's consistency.
    """
    if denominator == 0:
        return {"status": R1_NOT_EVALUABLE, "ratio": None, "numerator": 0, "denominator": 0, "threshold": threshold}
    ratio = numerator / denominator
    return {
        "status": "PASS" if ratio >= threshold else "FAIL",
        "ratio": ratio,
        "numerator": numerator,
        "denominator": denominator,
        "threshold": threshold,
    }


def _frame_routes(frames, d_decision_required: bool) -> tuple:
    """A36.4/A36.6's frame -> REQUIRED route map, conditioned by A27.3 (A48).

    Delegated to `build_oracle.frame_required_routes` rather than restated, for the reason the
    previous spelling already gave about the constants: two copies drift. Before A48 this
    function derived `human` from RAW D membership, so it demanded a human answer for every
    D-frame R1 pair even when A27.3 had made that route non-decision-bearing.
    """
    return _bo().frame_required_routes(frames, d_decision_required)


def _required_r1_routes(primary: dict, repeat: dict, repeat_bid: str, primary_bid: str, d_decision_required: bool) -> tuple:
    """The routes an R1 pair MUST be scored on, enforced against A36.6 rather than declared.

    A36.6: "The repeat remains ONE canonical `r1-repeat` identity and INHERITS its primary's
    required route(s): C only -> AI, D only -> human, C and D -> both." So frames must match and
    the repeat's declared routes must be exactly the frame-derived set.

    WHY THE PRIMARY'S OWN `adjudication_routes` IS NOT THE COMPARISON, and this is the one place
    the enforcement deliberately differs from a literal reading. A C-audit-selected primary carries
    `human` IN ADDITION to its frame routes, and `plan_r1_repeats` explicitly does NOT inherit
    `is_c_audit_selected` (`build_oracle`: `replace(s, is_r1_repeat=True, is_c_audit_selected=False)`).
    So for a C-only audited primary the two route sets legitimately DIFFER -- primary `(ai, human)`,
    repeat `(ai,)` -- and requiring equality would refuse a frozen, valid configuration that the
    real run will contain, since most C regions are not discordant and the audit draws 25 of them.
    The primary is therefore required to CONTAIN its frame routes, not to equal the repeat's.
    """
    if sorted(repeat.get("frames") or []) != sorted(primary.get("frames") or []):
        raise ScoreInputError(
            R1_FRAME_SET_MISMATCH,
            {
                "repeat_blind_id": repeat_bid,
                "primary_blind_id": primary_bid,
                "repeat_frames": repeat.get("frames"),
                "primary_frames": primary.get("frames"),
            },
        )
    expected = _frame_routes(repeat.get("frames") or [], d_decision_required)
    if tuple(repeat.get("adjudication_routes") or ()) != expected:
        raise ScoreInputError(
            R1_ROUTE_SET_MISMATCH,
            {
                "repeat_blind_id": repeat_bid,
                "declared": repeat.get("adjudication_routes"),
                "required_by_frames": list(expected),
                "frames": repeat.get("frames"),
            },
        )
    # The primary must at least carry its own frame routes; it may carry `human` on top when the
    # C audit drew it, which the repeat does not inherit.
    missing = [
        r for r in _frame_routes(primary.get("frames") or [], d_decision_required)
        if r not in (primary.get("adjudication_routes") or ())
    ]
    if missing:
        raise ScoreInputError(
            R1_ROUTE_SET_MISMATCH,
            {
                "primary_blind_id": primary_bid,
                "declared": primary.get("adjudication_routes"),
                "missing_frame_routes": missing,
            },
        )
    return expected


def r1_reliability(key: dict, adjudicated: dict) -> dict:
    """Section 5.6's R1 reliability, computed from committed artifacts. NOT a supplied scalar.

    RULED BY A41.2 R6. Section 5.6 froze the thresholds (text >= 0.90, role >= 0.80) and their
    consequences; A28.3/A28.4/A36.6 froze the repeat's identity, its 330 DPI, its inherited routes
    and its separately namespaced answers. R6 now fixes the computation:

        R6.1  denominator = the SYMMETRIC UNION of the complete primary and repeat enumerations,
              under ONE-TO-ONE matching on uniquely resolved A30 occurrence keys. An unresolved,
              one-sided or non-uniquely-resolved heading stays in the denominator and earns no
              numerator.
        R6.2  text agreement = EXACT equality of the values as returned. No normalisation.
        R6.3  role agreement = exact FINE section 5.3 role. UNREADABLE never agrees.
        R6.4  micro-average over heading occurrences within each required route; routes are
              evaluated separately and NEVER pooled; the gate is the WORST required route.

    No abstention status remains: R6 is ruled, so there is nothing left to defer.

    NO SCALAR INPUT EXISTS. A PASS requires committed pair-level evidence, which is why the
    `r1_role_agreement` parameter was removed rather than defaulted.
    """
    # A54 -- THE ONE EFFECTIVE-ROUTE OWNER. A48's `.get(..., True)` kept a pre-A48 key meaning
    # exactly what it meant when built, which is correct for every key but the frozen
    # confirmatory one: there it demands human answers on a route A27.3 had already made
    # non-decision-bearing, so no completed review could ever satisfy it. The owner re-derives
    # the budget from that key's OWN committed census and leaves its route bytes untouched.
    d_required = _bo().effective_d_decision_required(key)

    by_base = {}
    for bid, record in key["stimuli"].items():
        if record.get("control_kind") is not None or record.get("is_r1_repeat"):
            continue
        by_base[record["base_identity"]] = bid

    pairs = []
    for bid, record in sorted(key["stimuli"].items()):
        if not record.get("is_r1_repeat"):
            continue
        primary_bid = by_base.get(record.get("r1_base_identity"))
        if primary_bid is None:
            raise ScoreInputError(R1_PRIMARY_MISSING, {"repeat_blind_id": bid, "base": record.get("r1_base_identity")})
        # A36.6 ENFORCEMENT -- the required routes come from FRAME MEMBERSHIP, never from the
        # repeat's own declaration. A shortened repeat record plus a correspondingly shortened
        # answer set is internally coherent, so iterating what the repeat CLAIMS would let a
        # FAILING required route be deleted and the gate pass on the survivor.
        required = _required_r1_routes(key["stimuli"][primary_bid], record, bid, primary_bid, d_required)
        for route in required:
            if bid not in adjudicated.get(route, {}) or primary_bid not in adjudicated.get(route, {}):
                raise ScoreInputError(
                    ADJUDICATION_ROUTE_MISSING, {"blind_id": bid, "primary": primary_bid, "route": route}
                )
            pairs.append(_r1_pair_facts(key, adjudicated, primary_bid, repeat_bid=bid, route=route))

    per_route = {}
    for route in sorted({p["route"] for p in pairs}):
        rows = [p for p in pairs if p["route"] == route]
        total = sum(p["denominator"] for p in rows)
        per_route[route] = {
            "n_pairs": len(rows),
            "text": _r1_status(sum(p["n_text_agree"] for p in rows), total, R1_TEXT_THRESHOLD),
            "role": _r1_status(sum(p["n_role_agree"] for p in rows), total, R1_ROLE_THRESHOLD),
        }

    def worst(dimension: str, threshold: float) -> dict:
        """R6.4 -- any FAIL wins; else any NOT_EVALUABLE; else PASS.

        FAIL takes precedence over NOT_EVALUABLE deliberately: a route that demonstrably failed
        its reliability gate is evidence, and letting an unevaluable sibling route soften it would
        be the "one route masks the other" failure this ordering exists to prevent.
        """
        rows = {r: per_route[r][dimension] for r in per_route}
        if not rows:
            return {"status": R1_NOT_EVALUABLE, "by_route": {}, "threshold": threshold}
        statuses = [row["status"] for row in rows.values()]
        status = next((c for c in ("FAIL", R1_NOT_EVALUABLE) if c in statuses), "PASS")
        return {"status": status, "by_route": rows, "threshold": threshold}

    return {
        "n_pairs": len(pairs),
        "pairs": pairs,
        "per_route": per_route,
        "pooled_across_routes": False,
        "text": worst("text", R1_TEXT_THRESHOLD),
        "role": worst("role", R1_ROLE_THRESHOLD),
        "matching": "one-to-one on uniquely resolved A30 occurrence keys; symmetric union denominator (R6.1)",
        "text_comparison": "EXACT equality of the values as returned -- no normalisation (R6.2)",
        "role_comparison": "exact fine section 5.3 role; UNREADABLE never agrees (R6.3)",
        "aggregation": "heading-occurrence micro-average per route; worst required route (R6.4)",
        "ruled_by": "A41.2 R6",
        "decision_owner": "Rule 3 gate vector (A27.6); no consequence is applied here",
        # A48 -- PROVENANCE ON THE VALUE THAT MOVED. Over the A27.3 budget the D decision route
        # is not result-bearing, so A48 removes the human arm from R1's REQUIRED population and
        # the gate is computed on a different set of pairs than it would have been. That is a
        # value change caused by a post-boundary deviation, so section 4.7 attaches here.
        #
        # Within budget A48 changes nothing about which routes R1 requires, so no A48 status is
        # claimed: labelling it there would be the global relabelling 4.7 exists to prevent.
        "a48_required_routes_changed": not d_required,
        "confirmatory_status": None if d_required else A48_NON_CONFIRMATORY,
    }


#: A40's three control kinds, and the R8 verdict vocabulary.
CONTROL_KINDS = ("N-A", "N-B", "N-C")
#: A40.1 / §5.6's frozen control population: 8 N-A, 8 N-B, 4 N-C. Stated here so the scorer can
#: refuse an INCOMPLETE control artifact rather than certifying whatever rows it was handed -- a
#: coherent key missing one N-A would otherwise report 7/7 PASS and satisfy a Rule 3 blocker on a
#: population smaller than the one the protocol froze.
FROZEN_CONTROL_POPULATION = {"N-A": 8, "N-B": 8, "N-C": 4}


def frozen_control_routes() -> tuple[str, ...]:
    """Every control takes BOTH result-bearing routes (A36.6), so a control cannot be scored on one.

    Derived from `build_oracle`'s own constants rather than restated, so the two cannot drift.
    A function rather than a module constant only because that derivation is now lazy: evaluating
    it at module scope is exactly what would drag the renderer back into the import graph.
    """
    return (_bo().ROUTE_AI, _bo().ROUTE_HUMAN)


CONTROL_POPULATION_INCOMPLETE = "CONTROL_POPULATION_INCOMPLETE"
CONTROL_ROUTE_SET_MISMATCH = "CONTROL_ROUTE_SET_MISMATCH"
CONTROL_IDENTITY_DUPLICATED = "CONTROL_IDENTITY_DUPLICATED"
CONTROL_TARGET_ABSENT = "EXPECTED_TARGET_ABSENT"
CONTROL_TARGET_DUPLICATED = "EXPECTED_TARGET_DUPLICATED"
CONTROL_TARGET_UNREADABLE = "EXPECTED_TARGET_UNREADABLE"
CONTROL_HEADING_REPORTED = "HEADING_REPORTED_IN_HEADING_FREE_REGION"
CONTROL_TRUTH_MALFORMED = "CONTROL_TRUTH_MALFORMED"


def _control_verdict(kind: str, expected, answer: dict) -> dict:
    """One control fixture on ONE route: PASS/FAIL against its COMMITTED expected truth.

    RULED BY A41.2 R8. Comparison is **exact raw string equality**, never `m2_normalize`: a
    `WELD_TWO_WORDS` or `SPLIT_ONE_WORD` control differs from its source only in whitespace, so
    normalising here would make the control incapable of detecting the mutation it exists to test
    -- the fixture would pass whether or not the adjudicator saw the alteration.

    Other headings in the crop do NOT by themselves fail N-A or N-B. The committed truth
    establishes the TARGET occurrence, not a complete oracle for every heading the region may
    contain, and treating an extra heading as a failure would charge the control for the
    adjudicator's own correct enumeration.
    """
    texts = [h.get("text") for h in answer.get("headings", [])]
    if kind == "N-C":
        # Constructionally heading-free (A40): the answer must be exactly empty.
        reported = [t for t in texts]
        return {
            "pass": not answer.get("headings", []),
            "reason": None if not answer.get("headings", []) else CONTROL_HEADING_REPORTED,
            "observed_texts": reported,
            "expected": [],
        }

    if not isinstance(expected, list) or len(expected) != 1 or "text" not in (expected[0] or {}):
        raise ScoreInputError(CONTROL_TRUTH_MALFORMED, {"kind": kind, "expected": expected})
    target = expected[0]["text"]
    occurrences = sum(1 for t in texts if t == target)
    if occurrences == 1:
        return {"pass": True, "reason": None, "observed_texts": texts, "expected": [target]}
    if occurrences > 1:
        reason = CONTROL_TARGET_DUPLICATED
    elif _bo().UNREADABLE in texts:
        # Reported as its own reason rather than folded into "absent": an illegible stimulus is a
        # different finding from an adjudicator who read it and transcribed something else.
        reason = CONTROL_TARGET_UNREADABLE
    else:
        reason = CONTROL_TARGET_ABSENT
    return {"pass": False, "reason": reason, "observed_texts": texts, "expected": [target]}


def _validate_control_population(controls: dict) -> None:
    """The frozen 8 / 8 / 4 control census, both routes each, unique identities. Refuses otherwise.

    Each of these is a property of the INPUT ARTIFACT, not an observed control failure, which is
    why they raise rather than returning FAIL or NOT_EVALUABLE: a FAIL would report a finding about
    the adjudicator, and NOT_EVALUABLE would report one about the study, when the truth is that the
    artifact handed to the scorer is not the frozen population.

    A KEY WITH NO CONTROLS AT ALL IS NOT VALIDATED HERE, and that is deliberate rather than a hole.
    The self-certification risk the enforcement closes is a PARTIAL population reporting PASS: a
    key carrying 7 of 8 N-A controls would otherwise certify a Rule 3 blocker on a smaller census
    than the protocol froze. A key carrying NONE certifies nothing -- every kind reports
    `NOT_EVALUABLE`, which is not a PASS and cannot satisfy Rule 3 -- and refusing it outright would
    make the scorer unrunnable on exactly the DEVELOPMENT and mechanism material it must be tested
    against, including its own real-producer end-to-end check. The confirmatory key carries all 20
    by construction, so the frozen census is enforced wherever it exists.
    """
    if not controls:
        return
    for bid, record in controls.items():
        kind = record["control_kind"]
        if kind not in CONTROL_KINDS:
            raise ScoreInputError(CONTROL_TRUTH_MALFORMED, {"blind_id": bid, "control_kind": kind})
        # BOTH result-bearing routes, per A36.6. A control declaring one route could otherwise be
        # scored on the route that passes while the route whose labels are consumed goes unchecked.
        if tuple(record.get("adjudication_routes") or ()) != frozen_control_routes():
            raise ScoreInputError(
                CONTROL_ROUTE_SET_MISMATCH,
                {
                    "blind_id": bid,
                    "control_kind": kind,
                    "declared": record.get("adjudication_routes"),
                    "required": list(frozen_control_routes()),
                },
            )

    observed = {kind: sum(1 for r in controls.values() if r["control_kind"] == kind) for kind in CONTROL_KINDS}
    if observed != FROZEN_CONTROL_POPULATION:
        raise ScoreInputError(
            CONTROL_POPULATION_INCOMPLETE, {"observed": observed, "frozen": dict(FROZEN_CONTROL_POPULATION)}
        )

    # UNIQUE IDENTITIES, not merely the right COUNT: a duplicated or re-identified control keeps
    # the census looking correct while one real fixture goes unexercised and another is scored twice.
    identities = [r.get("canonical_identity") for r in controls.values()]
    duplicated = sorted({i for i in identities if identities.count(i) > 1})
    if duplicated or None in identities:
        raise ScoreInputError(
            CONTROL_IDENTITY_DUPLICATED,
            {"duplicated": duplicated[:4], "n_controls": len(controls), "n_unique": len(set(identities))},
        )


def control_verdicts(key: dict, adjudicated: dict) -> dict:
    """A41.2 R8 -- the N-A / N-B / N-C factual verdicts, from committed artifacts only.

    THE GAP THIS CLOSES. `control_fixtures.py` owns the fixtures (G6-gated) and
    `build_oracle.verify_join` binds each key record's carried truth to the manifest, but nothing
    compared an ADJUDICATED ANSWER to that truth -- so the three Rule 3 blockers had no factual
    verdict, and Phase 2 would have had to derive control truth on its own authority.

    Nothing is regenerated here: the chain is `control_fixtures.json` ->
    `build_oracle.control_expected_truth` -> committed key, joined to the committed adjudications.
    The G6 / `x26` binding is what makes the key's truth trustworthy, and is NOT reimplemented.

    EVERY REQUIRED ROUTE, separately (A36.6 gives a control both result-bearing routes). A kind
    PASSES only if EVERY fixture passes on EVERY required route -- no tolerance, no percentage.

    THE POPULATION IS VALIDATED FIRST, and an incomplete one REFUSES rather than being scored.
    "All rows present passed" is not the frozen question: a coherent key missing one N-A would
    report 7/7 PASS and satisfy a Rule 3 blocker on a smaller population than the protocol froze.
    `FROZEN_CONTROL_POPULATION` is the known 8/8/4 census (A40.1) and nothing here rebuilds it --
    no `control_fixtures` run, no source selection, no XML/PDF truth, no G6, no `x26`.
    """
    controls = {bid: r for bid, r in sorted(key["stimuli"].items()) if r.get("control_kind") is not None}
    _validate_control_population(controls)

    per_control = []
    for bid, record in controls.items():
        kind = record["control_kind"]
        for route in frozen_control_routes():
            answer = adjudicated.get(route, {}).get(bid)
            if answer is None:
                raise ScoreInputError(ADJUDICATION_ROUTE_MISSING, {"blind_id": bid, "route": route})
            verdict = _control_verdict(kind, record.get("control_expected_truth"), answer)
            per_control.append(
                {
                    "blind_id": bid,
                    "control_kind": kind,
                    "control_variant": record.get("control_variant"),
                    "route": route,
                    **verdict,
                }
            )

    by_kind = {}
    for kind in CONTROL_KINDS:
        rows = [r for r in per_control if r["control_kind"] == kind]
        passed = [r for r in rows if r["pass"]]
        by_kind[kind] = {
            "status": ("NOT_EVALUABLE" if not rows else ("PASS" if len(passed) == len(rows) else "FAIL")),
            "n_passed": len(passed),
            "n_total": len(rows),
            "by_route": {
                route: {
                    "n_passed": sum(1 for r in rows if r["route"] == route and r["pass"]),
                    "n_total": sum(1 for r in rows if r["route"] == route),
                }
                for route in sorted({r["route"] for r in rows})
            },
            "failures": [
                {k: r[k] for k in ("blind_id", "route", "control_variant", "reason", "observed_texts", "expected")}
                for r in rows
                if not r["pass"]
            ],
        }
    return {
        "per_control": per_control,
        "by_kind": by_kind,
        "n_controls": len({r["blind_id"] for r in per_control}),
        # Stated so a reader can tell "the frozen census was checked" from "there were no controls
        # to check": the second reports NOT_EVALUABLE everywhere and satisfies no Rule 3 blocker.
        "population_present": bool(controls),
        "frozen_population": dict(FROZEN_CONTROL_POPULATION),
        "required_routes": list(frozen_control_routes()),
        "comparison": "exact raw string equality; NO normalisation (R8)",
        "aggregation": "a kind PASSES only if every fixture passes on every required route; no tolerance",
        "ruled_by": "A41.2 R8",
        "decision_owner": "decide_architecture -- Rule 3's gate vector (A27.6) consumes these three "
        "statuses; they are the FACTS, not the decision",
    }


def _r1_role_gate(r1: dict) -> dict:
    """M5's section 6 gate, read from the COMPUTED R1 facts. No caller scalar can reach it."""
    return {
        "status": r1["role"]["status"],
        "threshold": R1_ROLE_THRESHOLD,
        "observed_by_route": r1["role"]["by_route"],
        # NOT_EVALUABLE is not a void either: section 6 voids M5 when role reliability falls BELOW
        # 0.80, which is a measurement, and an absent measurement is not one. Rule 3 owns what an
        # unevaluable gate does to the decision.
        "m5_void": r1["role"]["status"] == "FAIL",
        "owner": "Rule 3 gate vector (A27.6); this module reports the FACTS only",
        "evidence": "computed from the committed oracle key + adjudications, never supplied",
    }


def heading_metrics(inputs: ScoreInputs) -> dict:
    """M1-M5 per document per FRAME, plus the pooled rows. C and D are never pooled together.

    A36.3 -- "never pooled" means separate ESTIMANDS, not disjoint sets: one physical region may
    be in both frames and is then counted once in each. A36.4 fixes which answer each estimand
    reads, and this walks `PURPOSE_ROUTE` rather than choosing: C metrics read the AI answer even
    where a human answer exists, because D membership is conditional on the architectures
    disagreeing and taking human truth there would let them pick their own oracle.

    Controls and R1 repeats are EXCLUDED and counted. A control belongs to no estimand
    (`ESTIMAND_PURPOSES` names the purposes that read one, and no control purpose is in it); an
    R1 repeat re-presents its primary's region, so counting it would put one physical occurrence
    in a denominator twice.
    """
    per_document: dict[str, dict] = {}
    excluded = {"control": 0, "r1_repeat": 0}
    # A48 -- the D ESTIMAND IS CONSUMED ONLY WHILE ITS ROUTE IS RESULT-BEARING.
    #
    # A27.3: over the 60-region budget Rule 1 cannot choose X, so the complete-census human
    # decision evidence is not required and, by A48, is not even a required route. Walking
    # `(D_FRAME, PURPOSE_D_DECISION)` regardless demanded a human answer for every D primary
    # and refused the whole scorer -- the same defect as the route derivation, one layer down,
    # and the reason the first A48 pass read green: it exercised `validate_adjudicated` and
    # `r1_reliability` but never `score()`.
    #
    # The D rows are OMITTED, not zeroed. A zero M1-M5 block would state that the arms were
    # measured and agreed on nothing, which is empirical evidence this run never gathered.
    # `pooled` already drops a frame with no stimuli, so absence stays absence. The full D
    # census is untouched in the committed frames and in the decider's own budget reading.
    d_required = _bo().effective_d_decision_required(inputs.oracle_key)
    frame_purposes = ((_bo().C_FRAME, _bo().PURPOSE_C_METRICS),)
    if d_required:
        frame_purposes += ((_bo().D_FRAME, _bo().PURPOSE_D_DECISION),)
    # R1 is computed from the same committed artifacts, not supplied. M5's section 6 gate reads
    # this and nothing else, so no caller can hand the gate a verdict.
    r1 = r1_reliability(inputs.oracle_key, inputs.oracle_adjudicated)

    for bid, record in inputs.oracle_key["stimuli"].items():
        if record.get("control_kind") is not None:
            excluded["control"] += 1
            continue
        if record.get("is_r1_repeat"):
            excluded["r1_repeat"] += 1
            continue
        for frame_name, purpose in frame_purposes:
            if frame_name not in record["frames"]:
                continue
            route = _bo().PURPOSE_ROUTE[purpose]
            answer = inputs.oracle_adjudicated.get(route, {}).get(bid)
            if answer is None:
                raise ScoreInputError(ADJUDICATION_ROUTE_MISSING, {"blind_id": bid, "route": route})
            bucket = per_document.setdefault(record["document"], {})
            counts = bucket.setdefault(frame_name, _blank_heading_counts())
            _score_stimulus(counts, record, answer)

    documents = {}
    pooled = {frame_name: _blank_heading_counts() for frame_name, _p in frame_purposes}
    for document, by_frame in sorted(per_document.items()):
        documents[document] = {
            frame_name: _heading_metrics_from_counts(counts, r1) for frame_name, counts in sorted(by_frame.items())
        }
        for frame_name, counts in by_frame.items():
            _accumulate(pooled[frame_name], counts)
    return {
        "per_document": documents,
        "pooled": {
            frame_name: _heading_metrics_from_counts(counts, r1)
            for frame_name, counts in sorted(pooled.items())
            if counts["n_stimuli"]
        },
        "excluded_stimuli": excluded,
        "routing": {purpose: _bo().PURPOSE_ROUTE[purpose] for _f, purpose in frame_purposes},
        # A48 -- stated so a reader can tell "not measured because the budget forecloses Rule 1"
        # from "measured and empty". Absence of the D block below means the former.
        "d_decision_route_required": d_required,
        "d_estimand_status": "SCORED" if d_required else "NOT_OBSERVED -- A27.3 budget exceeded, "
        "Rule 1 cannot choose X and the complete-census human route is not result-bearing",
        "estimand_purposes": list(_bo().ESTIMAND_PURPOSES),
        "pooling_rule": "C and D are separate estimands (A36.3); they are never summed together",
        "r1": r1,
    }


def _accumulate(total: dict, part: dict) -> None:
    """Sum one document's raw counts into the pooled counts, recursively over the same shape."""
    for key, value in part.items():
        if isinstance(value, dict):
            _accumulate(total[key], value)
        else:
            total[key] = total[key] + value


# ------------------------------------------------------------------- section 8 statistics


def clopper_pearson_upper(events: int, n: int) -> float:
    """The exact one-sided 95 % Clopper-Pearson UPPER bound on pi. Unit = the DOCUMENT.

    A27.5 assigns this to `score_metrics`; `methodology_contracts` carries only the zero-event
    closed form, which is the frozen verbatim case and is delegated to it here rather than
    re-derived. For k > 0 the bound is the p solving `P(X <= k | n, p) = 0.05`, found by
    bisection with a FIXED iteration count so the value is reproducible bit for bit.

    No tolerance and no continuity correction: section 8.3 froze the exact procedure, and a
    "close enough" bound on a 14-document holdout is the one number the design cannot afford
    to soften.
    """
    if n < 1:
        raise ScoreInputError(MISSING_REQUIRED_FIELD, {"where": "clopper_pearson_upper", "n": n})
    if events < 0 or events > n:
        raise ScoreInputError(MISSING_REQUIRED_FIELD, {"where": "clopper_pearson_upper", "events": events, "n": n})
    if events == 0:
        return MC.zero_event_upper_bound(n)
    if events == n:
        return 1.0

    def cdf(p: float) -> float:
        return sum(math.comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(events + 1))

    lo, hi = 0.0, 1.0
    for _ in range(_CP_ITERATIONS):
        mid = (lo + hi) / 2
        if cdf(mid) > SECTION8_ALPHA:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def document_discordance_event(frame: dict) -> dict:
    """Section 8's per-DOCUMENT event: "has >= 1 heading-level H/X discordance".

    THE EVIDENCE, and why it is this one. `build_frames` commits, per region, whether the two
    arms' EMITTED ANCHOR SETS differ, and equality there is over the whole production `Anchor`
    value -- page, line, kind, text and division. So a heading either arm missed, placed
    differently, classed differently or read differently makes the sets differ, and that is
    exactly section 8.2's "produced identical heading output". It needs no oracle, and its
    predicate `H != X` is symmetric under swapping the arms.

    The line-level M0 predicates are deliberately NOT the event: a text or segmentation
    difference on an ordinary body line is not heading-level, and using it would answer a
    different question under section 8's name.
    """
    regions = [r for page in frame["pages"] for r in page["regions"]]
    discordant = [
        {"page_number": r["page_number"], "region_ordinal": r["region_ordinal"]}
        for r in regions
        if BF.ANCHOR_DISCORDANCE in r["d_reasons"]
    ]
    return {
        "document": frame["document"],
        "population": frame["population"],
        "event": bool(discordant),
        "n_anchor_discordant_regions": len(discordant),
        "evidence": discordant[:8],
        "definition": "any region whose emitted heading (Anchor) sets differ between H and X",
    }


def _qual_keys(qualification_label: dict, document: str) -> dict:
    """The I13 qualification and its A47.6 section 4.7 status, as ONE INSEPARABLE PAIR.

    A single helper, because the two keys are attached at six different result surfaces and
    the failure mode is that they drift apart at one of them. `qualification` is what a
    consumer reads to decide whether a result is PDFIUM-conditioned; `qualification_status`
    is what tells them the value arrived through the A45 post-boundary repair and is
    therefore non-confirmatory under section 4.7. A surface carrying the first without the
    second reads as an ordinary confirmatory result, which is exactly the mislabelling this
    slice exists to prevent.

    Takes the WHOLE label rather than the per-document map so the status cannot be dropped
    by a caller that only remembered to pass the labels.
    """
    return {
        "qualification": qualification_label.get("per_document", {}).get(document),
        "qualification_status": qualification_label.get("confirmatory_status"),
    }


def section8(frames, paired_metrics: dict, qualification_label: dict) -> dict:
    """The section 8 block: the estimand, the exact bound, the bootstrap, and the pairing.

    P-HEAD ONLY. `SECTION8_DOCUMENT_DISCORDANCE` carries the population in the statistic's own
    identity because section 4.4.1 claims no heading metric on P-robust; a P-robust document
    entering here would both inflate N and silently reuse a draw sequence that is not its own.

    ZERO EVENTS: the closed form `1 - 0.05 ** (1/N)` and NO bootstrap. 8.1 measured that every
    resample of an all-zero cluster set is zero, so the percentile interval is [0.0, 0.0] and
    carries no information about a new document -- in precisely the case this design expects.
    The wording below therefore says what was OBSERVED and never that the true rate is zero.
    """
    # CANONICALLY ORDERED, for the reason `canonical_document_vector` sorts: the caller's listing
    # order must not reach the artifact. An input-ordered list means two runs over the same
    # population print different per-document detail, and a reader comparing them sees a
    # difference where there is none.
    events = sorted(
        (
            # I13 -- every RQ1/RQ2 result computed on a cross-engine-failing document carries the
            # qualification, including this per-document event row. A47.6 adds the section 4.7
            # status beside it: the label and its deviation status must never separate, or a row
            # can be read as confirmatory by a consumer that only looks at `qualification`.
            {
                **document_discordance_event(f),
                **_qual_keys(qualification_label, f["document"]),
            }
            for f in frames
        ),
        key=lambda e: MC.canonical(e["document"]),
    )
    p_head = [e for e in events if e["population"] == MC.P_HEAD]
    excluded = [e["document"] for e in events if e["population"] != MC.P_HEAD]
    n = len(p_head)
    k = sum(1 for e in p_head if e["event"])

    block = {
        "estimand": "pi = P(a document from the target population shows >= 1 heading-level H/X discordance)",
        "independent_unit": "document",
        "population": MC.P_HEAD,
        "excluded_documents_not_p_head": sorted(excluded),
        "n_documents": n,
        "events": k,
        # THE STATISTIC POPULATION, and nothing else. The bound and the bootstrap were always
        # P-head only, but this vector previously carried every document -- so a reader (or a
        # later consumer) could take a P-robust row for a member of the statistic, and a
        # discordance on an excluded document would appear inside the very list that documents
        # `n_documents` and `events`. Section 4.4.1 claims NO heading metric on P-robust, so a
        # P-robust row in this vector is not a diagnostic, it is a category error.
        "per_document": p_head,
        # The excluded rows stay VISIBLE -- dropping them would hide that a document was held out
        # of a heading statistic at all -- but under a name that cannot be read as membership.
        "excluded_diagnostics_not_in_statistic": [e for e in events if e["population"] != MC.P_HEAD],
        "forbidden": "no per-heading probability; no heading-as-iid-trial denominator (section 8.3)",
        "paired": paired_metrics,
    }
    if n == 0:
        block["clopper_pearson_upper_bound"] = None
        block["status"] = VACUOUS
        block["bootstrap"] = {"reported": False, "reason": MC.EMPTY_DOCUMENT_SET, "gating": False}
        block["statement"] = "no P-head document was scored, so no bound is licensed"
        return block

    block["clopper_pearson_upper_bound"] = clopper_pearson_upper(k, n)
    block["clopper_pearson_alpha"] = SECTION8_ALPHA
    block["status"] = "REPORTED"
    # The A37 helper is the ONLY bootstrap surface (A38.10), and it derives the event count from
    # the vector rather than taking one, so a supplied count cannot contradict what it summarises.
    block["bootstrap"] = MC.section8_document_bootstrap([(e["document"], e["event"]) for e in p_head])
    block["zero_event_closed_form_used"] = k == 0
    if k == 0:
        block["statement"] = (
            f"no heading-level discordance observed on {n} P-head document(s); exact one-sided "
            f"95 % upper bound on the per-document rate: {block['clopper_pearson_upper_bound']:.4f}. "
            "This is an OBSERVATION, not a claim that the true rate is zero."
        )
    else:
        block["statement"] = (
            f"{k} of {n} P-head document(s) showed at least one heading-level discordance; exact "
            f"one-sided 95 % upper bound on the per-document rate: "
            f"{block['clopper_pearson_upper_bound']:.4f}."
        )
    return block


#: Per-arm scalars the paired comparison is defined over. Each is (metric path, arm subkey), so
#: adding one cannot silently change how the mean is weighted.
#: A41.2 **R9** -- the paired quantities, ruled. §8.3 requires per-document paired differences, an
#: unweighted mean over documents, and mandatory per-document detail, but never enumerates WHICH
#: quantities are paired. R9 fixes them as the two non-constant numeric M9 basis quantities.
#:
#: Both are defined on EVERY document for BOTH arms, so no missingness or vacuity policy has to be
#: invented -- which is exactly why M1-M5 and M7 are excluded: they can be VACUOUS, and pairing them
#: would need a new rule for which documents enter each mean. Also excluded, deliberately:
#: `derive_size_bands_returns_a_band` and `coverage_meets_floor` (booleans Rule 0 consumes, not
#: numeric differences), `coverage_floor` (a frozen constant), `n_margin_numbered_with_glyph_size`
#: (a diagnostic, not A39.1's quantity), and internal support counts that are not frozen result
#: quantities.
PAIRED_M9_QUANTITIES = ("n_margin_numbered_lines", "coverage")


def paired_differences(frames, qualification_label: dict) -> dict:
    """§8.3's paired comparison, over R9's two M9 basis quantities. Populations NEVER pooled.

    M9 is valid on P-head AND P-robust (§4.4.1 claims M0 and M9 on both), but the two populations
    are never pooled: a combined 17-document mean would mix a heading-bearing population with one
    the study claims no heading metric on. Each quantity is therefore reported per population, with
    its own per-document detail and its own unweighted mean, and there is no combined figure.

    UNWEIGHTED over documents, never by heading count: a bill with 40 headings and one with 2 are
    one document each. Per-document detail is mandatory and is never collapsed to the mean alone.
    """
    out = {
        "ruled_by": "A41.2 R9",
        "quantities": list(PAIRED_M9_QUANTITIES),
        "quantity_source": "frames.m9.{H,X} -- the numeric M9 bases (A38.8/A39.1)",
        "weighting": "UNWEIGHTED mean over documents (§8.3); never weighted by heading count",
        "difference": "X - H",
        "populations_never_pooled": True,
        "no_combined_mean": "P-head and P-robust are reported separately; no 17-document mean exists",
        "excluded_quantities": {
            "M1-M5, M7": "can be VACUOUS -- pairing them would require an unruled missingness policy",
            "derive_size_bands_returns_a_band, coverage_meets_floor": "booleans consumed by Rule 0, not numeric",
            "coverage_floor": "a frozen constant, so its difference is always zero",
            "n_margin_numbered_with_glyph_size": "a diagnostic; A39.1's quantity is the margin-line count",
        },
        "by_population": {},
    }
    for population in sorted({f["population"] for f in frames}):
        members = sorted(
            (f for f in frames if f["population"] == population), key=lambda f: MC.canonical(f["document"])
        )
        rows = {}
        for quantity in PAIRED_M9_QUANTITIES:
            details, values = [], []
            for frame in members:
                h, x = frame["m9"]["H"][quantity], frame["m9"]["X"][quantity]
                difference = x - h
                details.append(
                    {
                        "document": frame["document"],
                        "population": population,
                        "H": h,
                        "X": x,
                        "difference": difference,
                        # I13 -- the qualification travels with every result row it applies to,
                        # and A47.6 keeps its section 4.7 status alongside it.
                        **_qual_keys(qualification_label, frame["document"]),
                    }
                )
                values.append(difference)
            rows[quantity] = {
                "per_document": details,
                "n_documents": len(values),
                "unweighted_mean_difference": (sum(values) / len(values)) if values else None,
                "status": "REPORTED" if values else VACUOUS,
            }
        out["by_population"][population] = rows
    return out


# ------------------------------------------------------------------ section 4.5 adequacy


def adequacy_facts(frames, document_strata: dict) -> dict:
    """Section 4.5's inputs and the A28.2 state machine's verdict, as FACTS.

    A28.1 counts `|H_keys union X_keys|` over A27.1/A30.1 occurrence keys, restricted to P-head
    and to account/agency/grouping; A30.4 made both restrictions executable inside
    `filter_keys`, so they are applied by the owner rather than trusted to this caller.

    Rule 3's consumption of an INADEQUATE verdict belongs to `decide_architecture` (A27.6). The
    verdict is reported here because it is a property of the population, not of the decision.
    """
    keyed = []
    for frame in frames:
        for arm in ARMS:
            for row in frame["architecture_occurrences"][arm]:
                if row["occurrence_key"] is None:
                    continue
                keyed.append((_key_form(row["occurrence_key"]), row["anchor"]["kind"], frame["population"]))
    kept = MC.filter_keys(keyed)
    occurrences = MC.adequacy_occurrences(kept, set())
    strata = sorted({document_strata[f["document"]] for f in frames if f["document"] in document_strata})
    return {
        "kinds_counted": sorted(MC.ADEQUACY_KINDS),
        "population_counted": MC.P_HEAD,
        "n_occurrences": occurrences,
        "strata_filled": len(strata),
        "strata": strata,
        "verdict": MC.adequacy(len(strata), occurrences),
        "owner": "decide_architecture consumes this (Rule 3); the verdict itself is a population fact",
    }


# ----------------------------------------------------------------- cross-engine and S1


def qualification(cross_engine: dict) -> dict:
    """I13 -- the PDFIUM-CONDITIONED FRAME label, per document and for both headlines.

    The thresholds live in `x09_skeleton_cross_engine.gate` and reach here only through the
    committed artifact's `passed`; this module never re-derives 0.95 or 0.75. Failure on MORE
    than a third of sampled documents qualifies BOTH headlines. It never blocks (A27.6).
    """
    rows = cross_engine["per_document"]
    failed = [r["document"] for r in rows if not r["passed"]]
    return {
        "per_document": {r["document"]: (None if r["passed"] else PDFIUM_CONDITIONED_FRAME) for r in rows},
        "n_documents": len(rows),
        "n_failed": len(failed),
        "failed_documents": sorted(failed),
        # strictly MORE than one third, as x09's own consequence text states
        "both_headlines_qualified": len(failed) * 3 > len(rows),
        # BOTH headline qualifications, emitted explicitly rather than left to a reader to derive
        # from the boolean above. `None` on a headline means "not conditioned", not "unknown".
        "headline_qualifications": {
            "RQ1": PDFIUM_CONDITIONED_FRAME if len(failed) * 3 > len(rows) else None,
            "RQ2": PDFIUM_CONDITIONED_FRAME if len(failed) * 3 > len(rows) else None,
        },
        "decision_blocking": False,
        "threshold_owner": "x09_skeleton_cross_engine.gate (document 0.95 / page 0.75)",
        # A47.6 -- the section 4.7 status travels WITH the qualification, read from the
        # artifact exactly as `passed` is. The scorer does NOT consult the continuation record
        # itself: it is a CONSUMER of committed facts, and its frozen import allowlist exists
        # to keep it one. `validate_inputs` requires the field, so a cross-engine artifact
        # without provenance is refused rather than silently read as confirmatory.
        "confirmatory_status": cross_engine["confirmatory_status"],
    }


def s1_status(s1: dict) -> dict:
    """Section 5's first control: if S1 does not fire, M0 is NOT REPORTABLE.

    The consequence for the DECISION is A27.6's; the consequence for M0's reportability is
    section 5's own control row, so it is recorded here where M0 is produced.
    """
    fires = bool(s1.get("fires"))
    return {
        "fires": fires,
        "advance_scale": s1.get("advance_scale"),
        "m0_reportable": fires,
        "m0_status": "REPORTED" if fires else NOT_REPORTABLE_S1_DEAD,
        "gate_owner": "Rule 3 gate vector (A27.6)",
    }


# ------------------------------------------------------------------------------- the scorer


def score(inputs: ScoreInputs) -> dict:
    """The whole `metrics.json` payload. Facts only; no architecture outcome anywhere in it."""
    checked = validate_inputs(inputs)
    s1 = s1_status(inputs.s1)
    label = qualification(inputs.cross_engine)

    per_document = {}
    for frame in inputs.frames:
        document = frame["document"]
        block = m0_block(frame)
        if not s1["m0_reportable"]:
            # The comparator is not live, so the M0 rates are withheld rather than printed. The
            # raw counts stay: withholding evidence would be worse than withholding a rate.
            for name in ("M0a_text_rate", "M0b_segmentation_rate", "M0_any_rate", "M0b_rate_on_defined", "M0c_rate"):
                block[name] = {**block[name], "value": None, "status": NOT_REPORTABLE_S1_DEAD}
        # I13 -- the qualification is attached to EVERY applicable result surface, not only to a
        # parent block a consumer would have to remember to look up. A passing document carries an
        # explicit `None` rather than an absent field, so "not conditioned" and "nobody labelled
        # this" are distinguishable. It never reaches a decision input: cross-engine qualifies
        # REPORTING only (A27.6), which is why `decision_blocking` stays false and no gate or status
        # in this payload is derived from it.
        quals = _qual_keys(label, document)
        per_document[document] = {
            "population": frame["population"],
            "stratum": inputs.document_strata.get(document),
            **quals,
            "M0": {**block, **quals},
            "M7": {**m7_block(frame), **quals},
            "M9": {**rule0_facts(frame), **quals},
        }

    headings = heading_metrics(inputs)
    for document, metrics in headings["per_document"].items():
        quals = _qual_keys(label, document)
        per_document[document]["headings_by_frame"] = {
            frame_name: {**block, **quals} for frame_name, block in metrics.items()
        }
    paired = paired_differences(inputs.frames, label)

    return {
        "schema": SCHEMA,
        # ENUMERATED, not described by subtraction: "M0-M9 minus M6" names the deferred metric, and
        # the schema must not contain its name at all -- see the note on its absence below.
        "owns": "PRE-REGISTRATION section 6 metrics M0, M1, M2, M3, M4, M5, M7 and M9; section 8; "
        "HARNESS-PLAN section 5",
        "decision_taken_here": False,
        "decision_owner": "decide_architecture (section 7) -- no Rule 0/1/3 outcome appears here",
        # M6 IS ABSENT FROM THIS SCHEMA, not present-and-annotated. §5 owns "M0-M9 minus M6", so a
        # key named for it -- even one whose value says DEFERRED -- puts a deferred metric in a
        # result-bearing artifact and invites a consumer to read or reserve it. The explanation
        # lives in A41 and this module's prose, where it cannot be mistaken for a result.
        "documents_scored": checked["documents"],
        "s1": s1,
        "cross_engine_qualification": label,
        "per_document": per_document,
        "headings_pooled": headings["pooled"],
        "heading_population_notes": {
            "excluded_stimuli": headings["excluded_stimuli"],
            "routing": headings["routing"],
            "estimand_purposes": headings["estimand_purposes"],
            "pooling_rule": headings["pooling_rule"],
        },
        "section8": section8(inputs.frames, paired, label),
        "adequacy_4_5": adequacy_facts(inputs.frames, inputs.document_strata),
        # Section 5.6's reliability facts, computed from the committed artifacts. A Rule 3 gate
        # INPUT, never a Rule 3 verdict -- and no caller scalar can reach it.
        "r1_reliability": headings["r1"],
        # A41.2 R8 -- the N-A / N-B / N-C factual verdicts. Three more Rule 3 gate INPUTS; the
        # decider consumes the statuses, and no consequence is applied here.
        "control_verdicts": control_verdicts(inputs.oracle_key, inputs.oracle_adjudicated),
    }


def write_metrics(payload: dict, out_path: Path | None = None) -> Path:
    """Write the CANONICAL `metrics.json`. Refuses before a VALID execution boundary.

    Guarded by `build_oracle.assert_write_permitted`, the single authority the oracle, S1 and
    cross-engine writers already use, so no confirmatory artifact can be created under a weaker
    condition than the material it describes.
    """
    import json

    out_path = Path(out_path) if out_path else (EV / "results" / "metrics.json")
    _bo().assert_write_permitted(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))
    return out_path
