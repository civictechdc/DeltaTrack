"""build_oracle -- HARNESS-PLAN section 4. Builds the two oracle artifacts.

A35. IMPLEMENTS ALREADY-FROZEN RULES AND INTRODUCES NONE. Every rule here is fixed by
PRE-REGISTRATION 5.3-5.8, HARNESS-PLAN section 4, or A19-A34. Where a frozen source does not
determine an outcome-affecting choice the obligation is to STOP and report, never to settle it
here -- see A35.1's forward finding on the M5 coarsening map, which this module neither needs
nor chooses.

WHAT IT PRODUCES, and why the two artifacts differ deliberately:

  PRIVATE KEY   blind id -> canonical pre-blinding identity, document_sha256, page,
                region_ordinal, stratum, frames ["C"]/["D"]/["C","D"] (A36.2), the required
                adjudication routes, control/repeat bookkeeping, the H/X output
                the later join needs, renderer name + version, DPI, committed bbox in PDF
                points, PNG sha256, and the region-line bijection A35.2 records.

  BLIND FILE    ONLY the opaque id, the rendered image, and the question/codebook.

The blind file is what the adjudicator reads. If any private field reaches it, the study's
blinding claim is false, so `leakage_report` gates it executably and `x21` proves the gate can
FAIL by injecting forbidden content rather than trusting inspection.

WHAT THIS MODULE MUST NOT DO. It never renders from any architecture's text; never lets a
region's H/X content influence cropping; never reveals control status; never re-derives
geometry from the PDF; never pads, clips, intersects or repairs a committed bbox; and never
turns a refusal into a skipped stimulus or a smaller denominator.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import anchor_provenance as AP
import methodology_contracts as MC
import oracle_geometry as OG
import pymupdf

HERE = Path(__file__).resolve()
EV = HERE.parents[1]
PROMPT_PATH = HERE.parent / "adjudicator_prompt.md"

RENDERER_NAME = "MuPDF (pymupdf)"

C_FRAME = "C"
D_FRAME = "D"

# ------------------------------------------------------------------ refusal classes

DOCUMENT_IS_HOLDOUT = "DOCUMENT_IS_HOLDOUT"
HOLDOUT_BEFORE_EXECUTION_BOUNDARY = "HOLDOUT_BEFORE_EXECUTION_BOUNDARY"
PROMPT_MISSING = "PROMPT_MISSING"
PROMPT_LEAKS = "PROMPT_LEAKS"
PROMPT_ASKS_FORBIDDEN_QUESTION = "PROMPT_ASKS_FORBIDDEN_QUESTION"
PROMPT_MISSING_REQUIRED_INSTRUCTION = "PROMPT_MISSING_REQUIRED_INSTRUCTION"
#: A48 -- a frame that declares no D census cannot be built against.
D_CENSUS_MISSING = "D_CENSUS_MISSING"
BLIND_ARTIFACT_LEAKS = "BLIND_ARTIFACT_LEAKS"
WRONG_DPI_FOR_STIMULUS = "WRONG_DPI_FOR_STIMULUS"
R1_BBOX_DIFFERS_FROM_PRIMARY = "R1_BBOX_DIFFERS_FROM_PRIMARY"
EMPTY_RENDER = "EMPTY_RENDER"
RENDERED_WIDTH_DISAGREES = "RENDERED_WIDTH_DISAGREES"
START_LINE_OUT_OF_RANGE = "START_LINE_OUT_OF_RANGE"
CONFIRMATORY_WRITE_BEFORE_EXECUTION = "CONFIRMATORY_WRITE_BEFORE_EXECUTION"

MISSING_IDENTITY_CANDIDATES = "MISSING_IDENTITY_CANDIDATES"
MISSING_ARCHITECTURE_OCCURRENCES = "MISSING_ARCHITECTURE_OCCURRENCES"
OCCURRENCE_UNMATCHED = "OCCURRENCE_UNMATCHED"
UNKNOWN_BLIND_ID = "UNKNOWN_BLIND_ID"
ADJUDICATION_ID_MISMATCH = "ADJUDICATION_ID_MISMATCH"
ADJUDICATION_ROUTE_MISSING = "ADJUDICATION_ROUTE_MISSING"
UNKNOWN_ADJUDICATION_PURPOSE = "UNKNOWN_ADJUDICATION_PURPOSE"
ANSWER_MISSING_FOR_REQUIRED_ROUTE = "ANSWER_MISSING_FOR_REQUIRED_ROUTE"
ADJUDICATION_NOT_NAMESPACED = "ADJUDICATION_NOT_NAMESPACED"

# ----------------------------------------------------------- A36 C/D overlap semantics
#
# A35.5 STOPPED here because the frozen sources did not say what happens when a region is in
# BOTH frames. A36 ruled it, and the ruling is implemented rather than re-decided:
#
#   A36.1  C and D are INDEPENDENT predicates and may overlap. Neither frame drops the
#          overlap, the C draw is never replaced after observing D membership, and the frames
#          are never forced disjoint.
#   A36.2  one physical region is ONE stimulus identity -- one base identity, one 300-DPI
#          primary, one PNG, one blind id. A28.3 gains NO frame component; membership is
#          METADATA about a stimulus, carried as an explicit ordered list because the old
#          singular `frame` projection could not represent an overlap unambiguously.
#   A36.3  "never pooled" separates ESTIMANDS, not SETS: the region counts once in each
#          frame's own denominator, and |C union D| is never substituted for either.
#   A36.4  a stimulus and an adjudication are different objects, so one blind stimulus takes
#          both required routes, separately namespaced.
C_FRAME_ROUTE = "ai"
D_FRAME_ROUTE = "human"


def frame_required_routes(frames, d_decision_required: bool) -> tuple:
    """A36.4/A36.6's frame -> REQUIRED route map, conditioned by A27.3 (A48).

    THE SINGLE OWNER. `score_metrics` calls this rather than restating it, exactly as it
    already did for the constants, so the builder and the scorer cannot disagree about which
    routes an R1 pair must be scored on.

    `d_decision_required` is `MC.d_decision_route_required(realized D census)`. When it is
    False the D route is not result-bearing, so D membership alone creates neither a human
    decision requirement nor a human R1 arm. C is untouched: C -> AI always.
    """
    routes = set()
    if C_FRAME in frames:
        routes.add(C_FRAME_ROUTE)
    if D_FRAME in frames and d_decision_required:
        routes.add(D_FRAME_ROUTE)
    return tuple(r for r in ROUTE_ORDER if r in routes)

ROUTE_AI = "ai"
ROUTE_HUMAN = "human"
# Deterministic order, so a membership set can never serialize two ways.
ROUTE_ORDER = (ROUTE_AI, ROUTE_HUMAN)
FRAME_ORDER = (C_FRAME, D_FRAME)

PURPOSE_C_METRICS = "c_metrics"
PURPOSE_D_DECISION = "d_decision"
PURPOSE_C_AUDIT = "c_audit"

# A40 F5 -- a CONTROL is answered on both result-bearing routes (A36.6 requires every control
# class on every answer route) while belonging to NEITHER estimand. Two purposes rather than one
# because `PURPOSE_ROUTE` is a purpose->route function: a single "control" purpose could only
# name one route, and the control would silently be answered on half of them.
#
# ADDITIVE ONLY. No ordinary purpose, route, frame or denominator changes: a control has
# `frames == ()`, so `frame_routes` stays empty, `frame_counts` already filters controls out of
# C, D and the audit, and `select_c_audit`/`plan_r1_repeats` already exclude `control_kind`.
PURPOSE_CONTROL_AI = "control_ai"
PURPOSE_CONTROL_HUMAN = "control_human"

# A36.4's CRITICAL PROHIBITION, as a table rather than as prose a caller might not read.
# C metrics read the AI answer even where a human answer exists, because D membership is
# CONDITIONAL ON ARCHITECTURE DISAGREEMENT -- taking human truth only on C-and-D regions would
# make C a mixed oracle whose source is selected by H/X discordance, i.e. the architectures
# would be choosing their own oracle on exactly the regions where they disagree.
PURPOSE_ROUTE = {
    PURPOSE_C_METRICS: ROUTE_AI,
    PURPOSE_D_DECISION: ROUTE_HUMAN,
    PURPOSE_C_AUDIT: ROUTE_HUMAN,
    PURPOSE_CONTROL_AI: ROUTE_AI,
    PURPOSE_CONTROL_HUMAN: ROUTE_HUMAN,
}
#: The purposes that read an ESTIMAND's answer. Controls are deliberately absent: a control must
#: never enter a C or D denominator, and naming the set explicitly means a later scorer cannot
#: acquire one by iterating `PURPOSE_ROUTE`.
ESTIMAND_PURPOSES = (PURPOSE_C_METRICS, PURPOSE_D_DECISION, PURPOSE_C_AUDIT)

# A36.4 -- the adjudication artifact must carry two separately namespaced answer sets keyed by
# the SAME blind id. The schema requirement is frozen now; the artifact is not built here.
ADJUDICATION_NAMESPACES = (ROUTE_AI, ROUTE_HUMAN)

# ---------------------------------------------------------------- A54 / A27.3 / A48
#: The `frame_counts` member holding the realized D census.
D_FRAME_CENSUS_KEY = "d_frame"

#: THE ONE FROZEN PRE-A48 KEY, identified CRYPTOGRAPHICALLY over its complete content.
#:
#: An earlier spelling pinned a summary fingerprint (schema, stimulus count, prompt digest,
#: aggregate frame counts). That is not an identity: the entire `stimuli` mapping can change
#: while every summary field holds. Because `effective_record_routes` trusts
#: `human_answer_purposes`, a key could drop `c_audit` from one selected record, keep
#: `frame_counts.c_audit_selected == 25`, receive the exception, and validate 24 audit answers
#: while looking complete. The digest below covers every field, so any such edit denies it.
PRE_A48_FROZEN_KEY_SHA256 = "669722351f95977d35b06006afec37c9ae1c4754773735e9d061840e8ca49048"

def canonical_sha256(obj) -> str:
    """SHA-256 of the artifact's canonical serialization, computed by STREAMING.

    `write_artifacts` writes `json.dumps(..., indent=1, sort_keys=True)`, so encoding the parsed
    object with the same settings and hashing the chunks reproduces the committed FILE's digest
    exactly. `iterencode` yields fragments, so the 307 MB serialization is never materialized.
    """
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(indent=1, sort_keys=True).iterencode(obj):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def is_pre_a48_frozen_key(key: dict) -> bool:
    """Is this EXACTLY the frozen pre-A48 confirmatory artifact, byte for byte?

    A key carrying either A48 field is post-A48 and answers for itself; `score_metrics` already
    cross-checks that answer against the frozen predicate, so it never reaches the digest. Any
    other key is compared against the committed artifact's content hash in full. The compatibility
    path is a statement about ONE artifact that already exists, never a licence for a shape.

    DELIBERATELY UNCACHED. An earlier spelling memoized the verdict by `id(key)` while holding a
    strong reference, and claimed the identity re-check made a stale hit impossible. It does not:
    holding the reference prevents the id being RECYCLED onto a different object, and prevents
    nothing at all about the SAME object being mutated afterwards. A caller could pass the frozen
    key, be recognised, then drop `c_audit` from one selected record and have the next check hand
    back the cached True without re-reading a byte, after which route reinterpretation trusts the
    altered purposes and a 24-of-25 audit validates. The digest is therefore recomputed whenever
    identity is asked for. It costs about 18 s on the real artifact, which is acceptable for a
    one-time research execution and is the price of the answer being about the key as it IS.
    """
    if "d_decision_route_required" in key or "d_frame_census" in key:
        return False
    return canonical_sha256(key) == PRE_A48_FROZEN_KEY_SHA256


def effective_d_decision_required(key: dict) -> bool:
    """Is the D DECISION route result-bearing for this key? THE ONE OWNER.

    A48 gave the A27.3 predicate an executable owner but left `key.get(..., True)` at the
    consumers, so a pre-A48 key kept meaning what it meant when it was built. For every key but
    one that is correct. For the frozen confirmatory key it is not: its census is 13,992, A27.3
    has already made that route non-decision-bearing, and defaulting to True demands 15,417 human
    answers as a hard prerequisite for producing ANY metric on a route that cannot decide
    anything. That is not a conservative default, it is an unsatisfiable one.

    Post-A48 keys are unchanged: the stored field is returned, which is exactly what
    `.get(..., True)` returned. An unrecognised pre-A48-shaped key is also unchanged: True.
    """
    if "d_decision_route_required" in key:
        return bool(key["d_decision_route_required"])
    if is_pre_a48_frozen_key(key):
        return MC.d_decision_route_required(int(key["frame_counts"][D_FRAME_CENSUS_KEY]))
    return True


def effective_record_routes(record: dict, d_decision_required: bool, *, reinterpret: bool = False) -> tuple:
    """The routes this stimulus ACTUALLY requires an answer on. THE ONE OWNER.

    REINTERPRETATION IS OFF BY DEFAULT, and that is the whole safety property. For every key but
    the frozen artifact the stored `adjudication_routes` ARE the requirement, exactly as before
    A54, so ordinary post-A48 validation is untouched and a key whose stored routes disagree with
    its purposes is still judged on what it stored rather than being quietly re-derived into
    something validatable.

    Only for the frozen pre-A48 artifact are routes derived from `human_answer_purposes` through
    `PURPOSE_ROUTE`, dropping `d_decision` where A27.3 denies the route. A `c_audit` or
    `control_human` purpose keeps its human requirement regardless, which is what keeps all 25
    seeded C-audit items required even where they are also D-frame members. AI requirements never
    move: a route stored as `ai` stays required whatever the D budget does.
    """
    if not reinterpret:
        return tuple(record.get("adjudication_routes") or ())
    routes = set()
    for purpose in record.get("human_answer_purposes") or ():
        if purpose == PURPOSE_D_DECISION and not d_decision_required:
            continue
        route = PURPOSE_ROUTE.get(purpose)
        if route is not None:
            routes.add(route)
    if ROUTE_AI in (record.get("adjudication_routes") or ()):
        routes.add(ROUTE_AI)
    return tuple(r for r in ROUTE_ORDER if r in routes)



class OracleBuildError(Exception):
    """Construction cannot proceed as frozen. Deterministic, and never a value.

    Distinct from `OracleGeometryError`, which this module lets propagate UNCAUGHT: A33.4 and
    A33.1 require an unrepresentable page or bbox to abort the build, and catching either here
    to continue would be exactly the silent-reduction failure they exist to prevent.
    """

    def __init__(self, reason: str, detail=None):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason} {detail!r}")


# ------------------------------------------------------- the confirmatory holdout guard

# The confirmatory members. This module may not open one before the boundary is VALID: H/X
# have not been run on them, and running them early would spend the holdout before the
# protocol authorises execution.
#
# A43 -- DERIVED FROM THE COMMITTED MEMBERSHIP, never transcribed.
#
# This was a hand-written literal of 17 ids, and it was WRONG in both directions: 5 ids that
# are in no committed membership version, no contamination class and no exposure list (they
# appear nowhere in this study but in that literal), and 5 real frozen members absent from it.
# The consequence was not cosmetic. `assert_source_permitted` PERMITTED those 5 members
# pre-boundary, so the one gate standing between the holdout and an unauthorised extraction
# was open on 29 % of the population; and `realized_population` derives its label from the
# same set, so a confirmatory key would have described itself with the wrong membership.
#
# The repair is not a corrected literal -- that would leave the same drift possible tomorrow.
# The population already HAS a single committed authority, and F1/F2/F10/F11 already gate its
# integrity, so the guard is READ from it. Divergence is now unspellable rather than checked,
# and `assert_guard_matches_membership` keeps an executable assertion for the gate to run.
MEMBERSHIP_PATH = EV / "results" / "holdout_membership.json"
HOLDOUT_POPULATION_UNAVAILABLE = "HOLDOUT_POPULATION_UNAVAILABLE"
HOLDOUT_GUARD_DIVERGED = "HOLDOUT_GUARD_DIVERGED"


def _membership_ids(path: Path = MEMBERSHIP_PATH) -> frozenset[str]:
    """The frozen id set, read from the committed authority.

    An unreadable or empty authority RAISES rather than yielding an empty guard: an empty
    frozenset would make `holdout_member` return None for every document, which is precisely
    "the holdout is unguarded" wearing the appearance of a clean load.
    """
    try:
        ids = frozenset(m["id"] for m in json.loads(Path(path).read_text())["members"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OracleBuildError(HOLDOUT_POPULATION_UNAVAILABLE, {"path": str(path), "error": str(exc)}) from exc
    if not ids:
        raise OracleBuildError(HOLDOUT_POPULATION_UNAVAILABLE, {"path": str(path), "why": "no members"})
    return ids


HOLDOUT_GUARD = _membership_ids()


def assert_guard_matches_membership(path: Path = MEMBERSHIP_PATH) -> None:
    """The guard IS the committed population. Executable, so a gate can run it.

    Redundant while the guard is derived, and deliberately kept: it is what fails if anyone
    ever re-introduces an independent literal, and it is the check G5 runs.
    """
    frozen = _membership_ids(path)
    missing, extra = sorted(frozen - HOLDOUT_GUARD), sorted(HOLDOUT_GUARD - frozen)
    if missing or extra:
        raise OracleBuildError(HOLDOUT_GUARD_DIVERGED, {"unguarded_members": missing, "guarded_non_members": extra})


# Written only once execution is authorised. Its ABSENCE is the pre-execution state, so the
# canonical artifacts below may not be written while it does not exist.
EXECUTION_MARKER = EV / "results" / "EXECUTION-START.json"
# Every canonical CONFIRMATORY artifact, so oracle, S1 and cross-engine writers all sit behind
# one VALID-state requirement rather than three differently-strict ones.
CANONICAL_ARTIFACTS = {
    # A43 -- frames.json belongs in this set and was absent only because it had no writer.
    # It is derived from confirmatory H/X extraction over the holdout, so it is exactly the
    # kind of artifact this guard exists for; leaving it out would have let the first
    # confirmatory artifact be the one the write guard could not see.
    (EV / "results" / "frames.json").resolve(),
    (EV / "results" / "oracle_key.json").resolve(),
    (EV / "results" / "oracle_blind.json").resolve(),
    (EV / "results" / "oracle_adjudicated.json").resolve(),
    (EV / "results" / "s1_control.json").resolve(),
    (EV / "results" / "cross_engine_control.json").resolve(),
    (EV / "results" / "metrics.json").resolve(),
    (EV / "results" / "scores.json").resolve(),
}


def realized_population(documents: list[dict]) -> str:
    """What population the key was ACTUALLY built from, derived from its sources.

    A hardcoded label is a claim the artifact cannot check about itself, and this one would
    have followed a confirmatory run onto disk still reading "pre-execution".
    """
    members = sorted(
        {m for doc in documents if (m := holdout_member(doc["frame"]["document"], doc.get("pdf_path"))) is not None}
    )
    if not members:
        return "NON-HOLDOUT (development / synthetic)"
    return f"CONFIRMATORY HOLDOUT -- {len(members)} member(s): {', '.join(members)}"


def holdout_member(document_id: str, path=None) -> str | None:
    """The confirmatory member this source is, or None."""
    haystack = f"{document_id}|{path or ''}"
    return next((m for m in sorted(HOLDOUT_GUARD) if m in haystack), None)


def execution_boundary_state() -> str:
    """ABSENT | UNCOMMITTED | MUTATED | VALID -- delegated to x04, the SINGLE authority.

    Deliberately not a second, weaker definition. x04 already requires the marker to be
    write-once (exactly one modifying commit, and the current blob equal to the one that
    commit introduced), so a stray file on disk, an uncommitted marker, or an edited one all
    fail here for the same reasons they fail there. Imported lazily so this module does not
    take a hard dependency on the freeze-check probe at import time.
    """
    from x04_freeze_check import marker_state

    state, _boundary, _errors = marker_state()
    return state


def assert_source_permitted(document_id: str, path=None) -> None:
    """A38 repair -- the holdout is GATED, not permanently forbidden.

    The previous rule refused all 17 confirmatory members unconditionally, which meant the
    component G5 exists to freeze could never actually execute after authorization: the study
    would have reached its boundary and found its own oracle builder unable to open the
    population it was built for.

        non-holdout DEVELOPMENT/SYNTHETIC   -> permitted, pre-execution
        confirmatory holdout, state != VALID -> REFUSED
        confirmatory holdout, state == VALID -> permitted

    Authorization is the marker STATE, never mere path existence: an uncommitted or mutated
    `EXECUTION-START.json` unlocks nothing.
    """
    member = holdout_member(document_id, path)
    if member is None:
        return
    state = execution_boundary_state()
    if state != "VALID":
        raise OracleBuildError(
            HOLDOUT_BEFORE_EXECUTION_BOUNDARY,
            {"document": document_id, "member": member, "marker_state": state},
        )


# Back-compat alias: callers that mean "this must never be a holdout" keep the old refusal.
def assert_not_holdout(document_id: str, path=None) -> None:
    member = holdout_member(document_id, path)
    if member is not None:
        raise OracleBuildError(DOCUMENT_IS_HOLDOUT, {"document": document_id, "member": member})


def assert_write_permitted(out_path: Path) -> None:
    """A canonical confirmatory artifact may be written only behind a VALID boundary.

    Same authority and the same four states as source access, so an artifact cannot be created
    under a weaker condition than the material it describes.
    """
    if out_path.resolve() in CANONICAL_ARTIFACTS and execution_boundary_state() != "VALID":
        raise OracleBuildError(
            CONFIRMATORY_WRITE_BEFORE_EXECUTION,
            {"path": str(out_path), "marker_state": execution_boundary_state()},
        )


# ------------------------------------------------------------------------ the prompt gate

# Tokens that would tell the adjudicator something the blinding protocol forbids it to know.
# Matched case-insensitively on word boundaries, so "extended glyph" is caught while ordinary
# prose is not. `\bH\b`/`\bX\b` are deliberately included: the arms are named by single letters
# throughout this study, and a stray "arm H" in a prompt is exactly the leak that matters.
FORBIDDEN_PATTERNS = {
    "architecture": r"\barchitectures?\b",
    "hybrid": r"\bhybrid\b",
    "extended_glyph": r"\bextended[- ]glyph\b|\bextended glyph\b",
    "arm_letter_H": r"\barm\s+H\b|\bH[- ]arm\b|\bH\s*/\s*X\b",
    "arm_letter_X": r"\barm\s+X\b|\bX[- ]arm\b",
    "pdfium": r"\bpdfium\b|\bpypdfium2?\b",
    "frame": r"\b[CD][- ]frame\b|\bcoverage frame\b|\bdiscordance frame\b",
    "stratum": r"\bstrat(um|a)\b",
    "control_status": r"\bnegative control\b|\bcontrol (region|item|stimulus)\b|\bN-[ABC]\b",
    "repeat_status": r"\brepeat(ed)? (stimulus|region|item)\b|\bR1\b|\breliability repeat\b",
    "deltatrack": r"\bdeltatrack\b|\bbilltrax\b",
    "renderer_scale": r"\b300\s*dpi\b|\b330\s*dpi\b|\bdpi\b",
    "amount_attribution": r"\bamount[- ]account\b|\battribut\w*\s+(the\s+)?(amount|figure|dollar)",
}

# M6 is DEFERRED (A20). Asking would collect data the study may not use and could bias the
# heading answers, so a prompt that asks it is refused rather than merely noted.
M6_QUESTION_PATTERNS = {
    "which_account_gets_amount": r"which account\b|\bwhich heading\b.{0,40}\b(amount|figure|dollar|sum)",
    "attribute_amount": r"\battribute\b.{0,40}\b(amount|figure|dollar|sum)",
    "total_the_amounts": r"\b(total|sum up|add up)\b.{0,30}\b(amount|figure|dollar)s?\b",
}

# What the prompt MUST contain. A prompt silently missing the A33.5 instruction would still
# collect a `start_x_px`, just a differently-defined one, and nothing downstream could tell.
REQUIRED_INSTRUCTIONS = {
    "a33_5_visible_ink": r"first character's own visible ink",
    "a33_5_ignore_marks": r"strike-through",
    "a33_5_not_bounding_box": r"do \*\*not\*\* use a text-box|do not use a text-box",
    "identity_annotation_only": r"identify \*?\*?which\*?\*? occurrence|position annotation",
    "independent_adjudication": r"judged independently|independently of it",
    "start_physical_line": r"start_physical_line",
    "start_x_px": r"start_x_px",
    "exact_printed_text": r"[Tt]ranscribe exactly as printed",
    "immediate_parent": r"immediate parent heading",
    "role_codebook": r"`account`",
}


def scan_forbidden(text: str) -> list[dict]:
    """Every forbidden pattern that matches, with the matched text, so a hit is inspectable."""
    hits = []
    for name, pattern in FORBIDDEN_PATTERNS.items():
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            hits.append({"pattern": name, "matched": m.group(0)})
    return hits


def prompt_report(prompt_text: str) -> dict:
    """Leakage / completeness report for the adjudicator prompt. Data, not a decision."""
    return {
        "forbidden": scan_forbidden(prompt_text),
        "m6_questions": [
            {"pattern": n, "matched": m.group(0)}
            for n, p in M6_QUESTION_PATTERNS.items()
            for m in re.finditer(p, prompt_text, flags=re.IGNORECASE)
        ],
        "missing_required": sorted(
            n for n, p in REQUIRED_INSTRUCTIONS.items() if not re.search(p, prompt_text, flags=re.IGNORECASE)
        ),
    }


def load_prompt(path: Path = PROMPT_PATH) -> str:
    """Read the committed prompt and REFUSE it if it leaks, asks M6, or is incomplete."""
    if not path.exists():
        raise OracleBuildError(PROMPT_MISSING, {"path": str(path)})
    text = path.read_text()
    report = prompt_report(text)
    if report["forbidden"]:
        raise OracleBuildError(PROMPT_LEAKS, report["forbidden"])
    if report["m6_questions"]:
        raise OracleBuildError(PROMPT_ASKS_FORBIDDEN_QUESTION, report["m6_questions"])
    if report["missing_required"]:
        raise OracleBuildError(PROMPT_MISSING_REQUIRED_INSTRUCTION, report["missing_required"])
    return text


# --------------------------------------------------------------------- stimulus planning


@dataclass(frozen=True)
class StimulusSpec:
    """One planned stimulus, BEFORE any blind id exists.

    A28.3: selection and ranking consume the canonical identities on this object. The blind id
    is derived only after the whole realized set is settled, so nothing here may depend on it.
    """

    document_id: str
    document_sha256: str
    page_number: int
    region_ordinal: int
    frames: tuple[str, ...]
    stratum: str
    is_r1_repeat: bool = False
    is_c_audit_selected: bool = False
    #: A48 -- is the D DECISION route result-bearing for the realized census? Set once by
    #: `build` from `MC.d_decision_route_required`, never per-document and never by a caller.
    #: Defaults True so every existing construction keeps its pre-A48 meaning until `build`
    #: conditions it on the census it actually realized.
    d_decision_required: bool = True
    control_kind: str | None = None
    control_variant: str | None = None
    source_fixture_sha256: str | None = None
    # A40 F5 -- a control carries its OWN committed geometry, because it has no frame to read it
    # from. These come straight from the control manifest and are never re-derived: the bbox
    # handed to `render_region` is the one `control_fixtures` committed, so no later code decides
    # what to crop and no text search recovers a region.
    control_pdf_path: str | None = None
    control_bbox: tuple[float, ...] | None = None
    control_line_mapping: tuple[tuple[int, ...], ...] = ()
    #: A40 section 6 -- the private binding. Carried as JSON strings so the spec stays a frozen
    #: dataclass of hashable fields; they are written to the PRIVATE key only and are covered by
    #: `LEAKY_KEY_FIELDS`, so a leak into the blind artifact is a reported defect.
    control_record_digest: str | None = None
    control_expected_json: str | None = None

    @property
    def frame_routes(self) -> tuple[str, ...]:
        """A36.4 -- the routes implied by FRAME MEMBERSHIP alone. C -> AI, D -> human.

        This is what an R1 repeat inherits (A36.6). The audit is deliberately not here: it is a
        property of the primary presentation of a base identity, not of frame membership.
        """
        return frame_required_routes(self.frames, self.d_decision_required)

    @property
    def is_control(self) -> bool:
        return self.control_kind is not None

    @property
    def adjudication_routes(self) -> tuple[str, ...]:
        """Every route this stimulus must actually be answered on.

        A36.6 -- a CONTROL takes BOTH result-bearing routes while sitting in neither frame. It
        cannot inherit them from `frame_routes`, which is frame membership and would be empty;
        stating them here is what keeps a control outside C and D without giving it a frame it
        does not belong to.
        """
        if self.is_control:
            return ROUTE_ORDER
        routes = set(self.frame_routes)
        if self.is_c_audit_selected:
            routes.add(ROUTE_HUMAN)
        return tuple(r for r in ROUTE_ORDER if r in routes)

    @property
    def human_answer_purposes(self) -> tuple[str, ...]:
        """What the ONE human answer is consumed for. A36.5 lets a C-audit item reuse its D answer.

        The purposes are separate because the audit denominator is defined over `cframe-audit`
        selections only: a C-and-D human answer that was NOT independently drawn must never
        enlarge the audit.
        """
        if self.is_control:
            return (PURPOSE_CONTROL_HUMAN,)
        purposes = []
        if D_FRAME in self.frames and self.d_decision_required:
            purposes.append(PURPOSE_D_DECISION)
        if self.is_c_audit_selected:
            purposes.append(PURPOSE_C_AUDIT)
        return tuple(purposes)

    @property
    def n_human_tasks(self) -> int:
        """A36.5 -- ONE human task, however many purposes consume it. Same blind image."""
        return 1 if ROUTE_HUMAN in self.adjudication_routes else 0

    @property
    def base_identity(self):
        if self.control_kind is not None:
            return MC.control_stimulus_identity(
                self.control_kind,
                self.source_fixture_sha256 or self.document_sha256,
                self.page_number,
                self.region_ordinal,
                self.control_variant or "",
            )
        return MC.base_stimulus_identity(self.document_sha256, self.page_number, self.region_ordinal)

    @property
    def final_identity(self):
        """The identity the blind id and the presentation order are computed from."""
        return MC.r1_repeat_identity(self.base_identity) if self.is_r1_repeat else self.base_identity

    @property
    def dpi(self) -> int:
        return MC.required_dpi(self.is_r1_repeat)


def plan_document_stimuli(frame: dict, stratum: str) -> list[StimulusSpec]:
    """ONE stimulus per region that is in either frame, carrying its membership (A36.2).

    Reads the COMMITTED frame only. It does not re-decide membership -- `build_frames` already
    settled which regions are C and which are D, and re-deriving either here would let the
    oracle layer move a frame boundary the frame layer owns.

    A region in BOTH frames yields ONE stimulus with `frames == ("C", "D")`. It is NOT emitted
    twice: A28.3's base identity has no frame component, so a second instance would be the same
    canonical identity and A30.5 would abort. A36.2 rules that this is correct -- membership is
    metadata, identity is the region.
    """
    specs = []
    for page_frame in frame["pages"]:
        for region in page_frame["regions"]:
            frames = tuple(name for flag, name in ((region["c_frame"], C_FRAME), (region["d_frame"], D_FRAME)) if flag)
            if not frames:
                continue
            specs.append(
                StimulusSpec(
                    document_id=frame["document"],
                    document_sha256=frame["document_sha256"],
                    page_number=page_frame["page_number"],
                    region_ordinal=region["region_ordinal"],
                    frames=frames,
                    stratum=stratum,
                )
            )
    return specs


CONTROL_STRATUM = "CONTROL"
CONTROL_MANIFEST_INCOMPLETE = "CONTROL_MANIFEST_INCOMPLETE"


def control_specs(manifest: dict, fixtures_root: Path, repo_root: Path) -> list[StimulusSpec]:
    """A40 F5 -- the manifest->spec adapter that was missing, and whose absence WAS F5.

    Every field comes from the committed manifest. Nothing is re-derived, nothing is searched
    for, and no parallel renderer or blind pipeline exists: the specs returned here go through
    the same `build()` as ordinary stimuli and are rendered by the same `render_region`.

    A control is `frames=()`, so it is in neither estimand; `select_c_audit` and
    `plan_r1_repeats` already exclude `control_kind`, so it can be neither audited nor repeated.
    """
    specs = []
    for f in manifest.get("fixtures", []):
        kind = f.get("control_kind")
        rel = f.get("generated_path") or f.get("source_path")
        bbox = f.get("region_bbox_pdf_points")
        if not kind or not rel or not bbox:
            raise OracleBuildError(
                CONTROL_MANIFEST_INCOMPLETE,
                {"identity": f.get("canonical_identity"), "kind": kind, "path": rel, "bbox": bbox},
            )
        root = fixtures_root if f.get("generated_path") else repo_root
        specs.append(
            StimulusSpec(
                document_id=f"CONTROL/{kind}/{f['schedule_index']}",
                document_sha256=f["source_sha256"],
                page_number=f["page_number"],
                region_ordinal=f["schedule_index"],
                frames=(),
                stratum=CONTROL_STRATUM,
                control_kind=kind,
                control_variant=f.get("variant"),
                source_fixture_sha256=f["source_sha256"],
                control_pdf_path=str(root / rel),
                control_bbox=tuple(float(v) for v in bbox),
                control_line_mapping=tuple(tuple(m) for m in (f.get("region_line_mapping") or ())),
                control_record_digest=control_record_digest(f),
                control_expected_json=json.dumps(f.get("expected_adjudicated_headings"), sort_keys=True),
            )
        )
    return specs


C_AUDIT_SIZE = 25


def select_c_audit(primaries: list[StimulusSpec], k: int = C_AUDIT_SIZE) -> list[StimulusSpec]:
    """A36.5 -- the 25-item human audit, ranked by `cframe-audit` over C BASE identities.

    D membership may not enter this function in any way. It does not appear in the candidate
    predicate (C membership only), in the ranked item (the base identity), or in `k`. That is
    what makes the audit sample and its denominator INVARIANT under D membership, which x21
    falsifies by marking many non-audit C regions as D and requiring the selection to stay
    byte-identical.
    """
    candidates = [s for s in primaries if C_FRAME in s.frames and not s.is_r1_repeat and s.control_kind is None]
    chosen = set(map(MC.canonical, MC.select("cframe-audit", [s.base_identity for s in candidates], k)))
    return [s for s in candidates if MC.canonical(s.base_identity) in chosen]


def apply_c_audit(primaries: list[StimulusSpec], k: int = C_AUDIT_SIZE) -> list[StimulusSpec]:
    """Mark the audit-selected primaries, preserving every other field."""
    chosen = {MC.canonical(s.base_identity) for s in select_c_audit(primaries, k)}
    return [replace(s, is_c_audit_selected=True) if MC.canonical(s.base_identity) in chosen else s for s in primaries]


def plan_r1_repeats(primaries: list[StimulusSpec], fraction: float = 0.10) -> list[StimulusSpec]:
    """5.6 R1 -- 10 % of regions presented twice, ranked by CANONICAL BASE identity (A28.3).

    The repeat carries the SAME committed bbox and source region; only the raster scale differs
    (A28.4). It records its own `start_x_px` later and resolves independently, so nothing here
    copies a primary's coordinate or resolved identity.

    A36.6 -- the repeat INHERITS its primary's frame membership, and therefore its frame routes:
    C only -> AI, D only -> human, C and D -> both. There are NO route-specific R1 identities;
    one physical repeat presented to two adjudicators yields two namespaced answers, not two
    stimuli. `is_c_audit_selected` is deliberately NOT inherited: the audit is 25 items drawn
    over base identities, and auditing the repeat as well would make it 26 tasks for 25 items.
    """
    eligible = [s for s in primaries if s.control_kind is None]
    k = math.floor(len(eligible) * fraction)
    chosen = set(map(MC.canonical, MC.select("r1-repeat", [s.base_identity for s in eligible], k)))
    return [
        replace(s, is_r1_repeat=True, is_c_audit_selected=False)
        for s in eligible
        if MC.canonical(s.base_identity) in chosen
    ]


def select_answer(record: dict, purpose: str, answers: dict):
    """A36.4 -- read the answer from the route the PURPOSE mandates, never the one that exists.

    The prohibition this enforces: C metrics take the AI answer even where a human answer is
    present. Falling back to human on C-and-D regions would make C a mixed oracle whose source
    is selected by H/X discordance. So a missing mandated answer REFUSES; it never substitutes.
    """
    if purpose not in PURPOSE_ROUTE:
        raise OracleBuildError(UNKNOWN_ADJUDICATION_PURPOSE, {"purpose": purpose})
    route = PURPOSE_ROUTE[purpose]
    if route not in record.get("adjudication_routes", ()):
        raise OracleBuildError(
            ANSWER_MISSING_FOR_REQUIRED_ROUTE,
            {"purpose": purpose, "route": route, "routes": record.get("adjudication_routes")},
        )
    if route not in answers or answers[route] is None:
        raise OracleBuildError(ANSWER_MISSING_FOR_REQUIRED_ROUTE, {"purpose": purpose, "route": route})
    return answers[route]


def validate_adjudication_namespacing(adjudicated: dict) -> None:
    """A36.4 -- the adjudication artifact must namespace answers by route under one blind id.

    Frozen as a SCHEMA REQUIREMENT now so the artifact cannot later be built with one flat
    answer per id, which would silently collapse a C-and-D region's two independent answers
    into whichever was written last.
    """
    missing = [ns for ns in ADJUDICATION_NAMESPACES if ns not in adjudicated]
    if missing:
        raise OracleBuildError(ADJUDICATION_NOT_NAMESPACED, {"missing_namespaces": missing})
    for ns in ADJUDICATION_NAMESPACES:
        if not isinstance(adjudicated[ns], dict):
            raise OracleBuildError(ADJUDICATION_NOT_NAMESPACED, {"namespace": ns, "type": type(adjudicated[ns])})


def presentation_order(specs: list[StimulusSpec]) -> list[StimulusSpec]:
    """A27.7/A28.3 `blind-order` -- ranks canonical FINAL INSTANCE identities.

    NO ranking consumes a blind id. `x21` proves it by re-deriving this order under a wholly
    different blind-id scheme and requiring it not to move.
    """
    ranked = MC.order("blind-order", [s.final_identity for s in specs])
    by_identity = {MC.canonical(s.final_identity): s for s in specs}
    return [by_identity[MC.canonical(i)] for i in ranked]


# ------------------------------------------------------------------------- rendering

# A35.2 -- the adjudicator reports a 1-based printed-line index within the rendered stimulus.
# The builder maps it onto the region's committed neutral lines, which are consecutive by
# ordinal and therefore top-to-bottom in the crop. `region_line_bijection` records the map so a
# reviewer can check it rather than take it on trust.


def region_line_bijection(page_frame: dict, region_ordinal: int) -> list[list]:
    """Committed neutral-line keys of the region, in printed top-to-bottom order.

    Index i (0-based) is `start_physical_line` i+1. Ordinal ordering IS printed ordering:
    `cluster_page` assigns ordinals by descending baseline, top to bottom.
    """
    region = next((r for r in page_frame["regions"] if r["region_ordinal"] == region_ordinal), None)
    if region is None:
        raise OracleBuildError(START_LINE_OUT_OF_RANGE, {"region_ordinal": region_ordinal})
    keys = [tuple(k) for k in region["neutral_line_keys"]]
    return [list(k) for k in sorted(keys, key=lambda k: k[1])]


def region_identity_candidates(page_frame: dict, region_ordinal: int) -> dict:
    """A38.2/A38.6 -- `{line_key: [(ngid, x0), ...]}` for the region's committed lines.

    Carried rather than referenced so the private key alone is sufficient for the occurrence
    join: a cross-artifact lookup could silently bind an adjudication to a frame the key was
    not built from, and nothing downstream would see it.
    """
    region = next((r for r in page_frame["regions"] if r["region_ordinal"] == region_ordinal), None)
    if region is None:
        raise OracleBuildError(START_LINE_OUT_OF_RANGE, {"region_ordinal": region_ordinal})
    wanted = {tuple(k) for k in region["neutral_line_keys"]}
    out = {}
    for line in page_frame["neutral_lines"]:
        key = tuple(line["key"])
        if key in wanted:
            candidates = line.get("identity_candidates")
            if candidates is None:
                raise OracleBuildError(MISSING_IDENTITY_CANDIDATES, {"line": list(key)})
            out[f"{key[0]}:{key[1]}"] = [[c["ngid"], c["x0"]] for c in candidates]
    if len(out) != len(wanted):
        raise OracleBuildError(MISSING_IDENTITY_CANDIDATES, {"expected": len(wanted), "found": len(out)})
    return out


def region_architecture_occurrences(frame: dict, page_number: int, region_ordinal: int) -> dict:
    """A38.3/A38.6 -- each arm's occurrence records for THIS PAGE and this region.

    THE PAIR, NOT THE ORDINAL. Region ordinals restart on every page, so filtering on
    `region_ordinal` alone gave a stimulus at (page P, region 0) the region-0 occurrences of
    EVERY page in the document. That is cross-page contamination: the emitted side of the M1-M5
    join would have contained headings printed on pages the adjudicator never saw, inflating
    the emitted set and corrupting precision on every multi-page document.

    An UNMATCHED occurrence is carried through, never filtered: it is evidence that production
    emitted a heading the A30 bridge could not key, and dropping it would shrink an M1-M5
    denominator with nothing to show for it. Its physical placement comes from A28.5 and does
    not depend on identity having succeeded.
    """
    occurrences = frame.get("architecture_occurrences")
    if not occurrences:
        raise OracleBuildError(MISSING_ARCHITECTURE_OCCURRENCES, {"document": frame.get("document")})
    return {
        arm: [r for r in rows if r["page_number"] == page_number and r["region_ordinal"] == region_ordinal]
        for arm, rows in sorted(occurrences.items())
    }


def resolve_start_line(bijection: list[list], start_physical_line: int) -> tuple:
    """1-based printed-line index -> committed neutral-line key. REFUSES out of range.

    A guess here would silently resolve a heading against the wrong physical line and the
    nearest-x resolver would then return a confident, wrong `start_ngid`.
    """
    if not isinstance(start_physical_line, int) or not 1 <= start_physical_line <= len(bijection):
        raise OracleBuildError(
            START_LINE_OUT_OF_RANGE, {"reported": start_physical_line, "region_lines": len(bijection)}
        )
    return tuple(bijection[start_physical_line - 1])


def render_region(page, bbox, dpi: int) -> tuple[bytes, int, int]:
    """Render exactly the committed bbox at exactly `dpi`. PDF geometry only.

    I6 -- no arm's text reaches the renderer: the only inputs are the page object, the bbox
    read from the committed frame, and the frozen DPI.
    """
    OG.check_rotation(page.rotation)
    OG.validate_region_bbox_for_page(bbox, page.rect.width, page.rect.height)
    height = page.rect.height
    # the frame's bboxes are PDF coordinates (y grows up); pymupdf Rect is top-left origin
    clip = pymupdf.Rect(bbox[0], height - bbox[3], bbox[2], height - bbox[1])
    scale = OG.scale(dpi)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
    if pix.width == 0 or pix.height == 0:
        raise OracleBuildError(EMPTY_RENDER, {"bbox": list(bbox), "dpi": dpi})
    expected = OG.expected_image_width(bbox[0], bbox[2], dpi)
    if pix.width != expected:
        raise OracleBuildError(RENDERED_WIDTH_DISAGREES, {"got": pix.width, "expected": expected, "dpi": dpi})
    return pix.tobytes("png"), pix.width, pix.height


# ------------------------------------------------------------------------ the two artifacts

# Everything the blind file is allowed to carry. An allowlist, not a denylist: a denylist
# silently passes any field added later, which is the failure mode that matters here.
BLIND_ALLOWED_KEYS = frozenset({"id", "image", "question"})

# Private-key fields whose appearance in the blind file would break blinding.
LEAKY_KEY_FIELDS = (
    "canonical_identity",
    "document",
    "document_sha256",
    "page_number",
    "region_ordinal",
    "stratum",
    "frames",
    "adjudication_routes",
    "human_answer_purposes",
    "is_c_audit_selected",
    "control_kind",
    "control_variant",
    "is_r1_repeat",
    "dpi",
    "bbox_pdf_points",
    "png_sha256",
    "architecture_occurrences",
    "identity_candidates",
    "region_line_bijection",
    # A40 section 6 -- the control binding is private too. The expected heading text is the one
    # value whose leak would tell an adjudicator the answer, so it is checked by VALUE here as
    # well as being structurally excluded by the blind allowlist.
    "control_source_fixture_sha256",
    "control_record_digest",
    "control_pdf_path",
    "control_expected_truth",
)


@dataclass
class BuildResult:
    key: dict = field(default_factory=dict)
    blind: dict = field(default_factory=dict)
    images: dict = field(default_factory=dict)


def leakage_report(blind: dict, key: dict) -> dict:
    """Does the adjudicator-facing artifact leak anything private? DATA, not a decision.

    Two independent checks, because either alone passes for the wrong reason:
      * a STRUCTURAL check that every item carries only allowlisted keys;
      * a VALUE check that no private value appears anywhere in the blind file's serialization,
        which catches a leak smuggled inside an allowed field.
    """
    serialized = json.dumps(blind, sort_keys=True)
    unexpected = sorted({k for item in blind.get("items", []) for k in item if k not in BLIND_ALLOWED_KEYS})

    leaked_values = []
    for bid, record in key.get("stimuli", {}).items():
        for fname in LEAKY_KEY_FIELDS:
            if fname not in record:
                continue
            value = record[fname]
            for token in _leak_tokens(value):
                if token and token in serialized:
                    leaked_values.append({"blind_id": bid, "field": fname, "token": token})
    return {
        "unexpected_keys": unexpected,
        "leaked_values": leaked_values[:20],
        "n_leaked_values": len(leaked_values),
        "forbidden_text": scan_forbidden(serialized),
    }


# The role/kind vocabulary is a CLOSED set the prompt publishes on purpose (5.3's codebook),
# and production's `AnchorKind` uses the same words. A term drawn from it therefore cannot
# identify WHICH stimulus an item is, so matching one in the blind file is not leakage -- it is
# the codebook doing its job. Excluded by membership rather than by spelling, so a kind added
# to either map is covered automatically instead of reappearing as a false positive.
PUBLISHED_VOCABULARY = frozenset(MC.ORACLE_ROLE_TO_M5) | frozenset(MC.EMITTED_KIND_TO_M5)


def _leak_tokens(value) -> list[str]:
    """Serialized forms of a private value worth searching for in the blind file.

    Short numeric values (a page number, a region ordinal) are deliberately EXCLUDED: "3"
    occurs in unrelated text and would make the gate fire constantly, which is how a gate stops
    being read. Structural allowlisting is what catches those, and `x21` proves it does.
    """
    if isinstance(value, str):
        return [value] if len(value) >= 8 and value not in PUBLISHED_VOCABULARY else []
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return []
    if isinstance(value, (list, tuple)):
        return [t for v in value for t in _leak_tokens(v)]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _leak_tokens(v)]
    return []


def build(
    documents: list[dict],
    *,
    prompt_path: Path = PROMPT_PATH,
    r1_fraction: float = 0.10,
    c_audit_size: int = C_AUDIT_SIZE,
    controls: list[StimulusSpec] | None = None,
) -> BuildResult:
    """Build the private key and the blind file for a set of documents.

    `documents` items: {"frame", "pdf_path", "stratum"}.

    A38.6 -- THE ARCHITECTURE OCCURRENCES COME FROM THE COMMITTED FRAME, and from nowhere else.
    There is deliberately no caller-supplied `architecture_output`: an optional blob had no
    schema, no deterministic producer, and could contradict the frame it claimed to describe
    with nothing able to detect the disagreement. One source of truth, or the join is a guess.
    All of it is written to the PRIVATE key and never reaches the renderer or the blind file.

    Nothing is adjudicated here and nothing is scored. Refusals propagate and abort: a stimulus
    is never dropped and no denominator is ever reduced.
    """
    question = load_prompt(prompt_path)

    specs: list[StimulusSpec] = []
    for doc in documents:
        frame = doc["frame"]
        assert_source_permitted(frame["document"], doc.get("pdf_path"))
        specs.extend(plan_document_stimuli(frame, doc.get("stratum", "UNSTRATIFIED")))
    specs.extend(controls or [])

    # A48 -- CONDITION THE D DECISION ROUTE ON THE REALIZED CENSUS, once, here.
    #
    # The census is the COMMITTED per-document `counts["d_frame_census"]`, the same quantity
    # `decide_architecture.d_frame_budget` reads, so the builder and the decider cannot be
    # looking at different censuses. It is summed over the documents actually built, never
    # over the specs: a spec set is post-audit and post-repeat, and A27.3's item is a region
    # of the census enumerated BEFORE any sampling.
    #
    # Applied before `apply_c_audit` and `plan_r1_repeats` so repeats inherit it through
    # `replace` rather than acquiring a route their primary does not have (A36.6).
    # FAIL CLOSED. A missing count is NOT zero: zero is within budget, so a frame that simply
    # forgot to declare its census would silently make the D decision route non-required and
    # excuse the very evidence Rule 1 needs. Absent means unusable, not empty.
    d_census = 0
    for doc in documents:
        counts = doc["frame"].get("counts") or {}
        if "d_frame_census" not in counts:
            raise OracleBuildError(D_CENSUS_MISSING, {"document": doc["frame"].get("document")})
        d_census += int(counts["d_frame_census"])
    d_required = MC.d_decision_route_required(d_census)
    specs = [replace(s, d_decision_required=d_required) for s in specs]
    # A36.5 -- the audit is drawn over C base identities BEFORE repeats exist, and never sees
    # D membership. Repeats are added after, and do not inherit audit selection (A36.6).
    specs = apply_c_audit(specs, c_audit_size)
    specs.extend(plan_r1_repeats(specs, r1_fraction))

    # I14 / A30.5 -- asserted over the COMPLETE realized set, controls and repeats included,
    # BEFORE anything is rendered or written. Raises on collision; never salts or re-rolls.
    MC.assert_realized_blind_ids_unique([s.final_identity for s in specs])

    ordered = presentation_order(specs)

    frames_by_doc = {d["frame"]["document"]: d for d in documents}
    result = BuildResult()
    key_stimuli: dict = {}
    blind_items: list[dict] = []

    open_docs: dict = {}
    try:
        for rank, spec in enumerate(ordered):
            if spec.is_control:
                # A40 F5 -- a control has no frame, so its geometry comes from the CONTROL
                # MANIFEST. Same renderer, same DPI rule, same blinding; only the provenance of
                # the bbox differs, and it is committed rather than derived. The three
                # frame-only join fields stay None: faking an architecture occurrence for a
                # control would invent the very evidence the control exists to bound.
                bbox = tuple(float(v) for v in spec.control_bbox)
                path = spec.control_pdf_path
                bijection = [list(m) for m in spec.control_line_mapping]
                candidates, occurrences = None, None
            else:
                doc = frames_by_doc[spec.document_id]
                frame = doc["frame"]
                page_frame = next(p for p in frame["pages"] if p["page_number"] == spec.page_number)

                # A33.1/A33.2 -- geometry from the COMMITTED frame, never re-derived from the PDF
                bbox = OG.region_bbox(page_frame, spec.region_ordinal)
                path = str(doc["pdf_path"])
                bijection = region_line_bijection(page_frame, spec.region_ordinal)
                candidates = region_identity_candidates(page_frame, spec.region_ordinal)
                occurrences = region_architecture_occurrences(frame, spec.page_number, spec.region_ordinal)

            if path not in open_docs:
                open_docs[path] = pymupdf.open(path)
            page = open_docs[path][spec.page_number - 1]

            png, width, height = render_region(page, bbox, spec.dpi)
            bid = MC.blind_id(spec.final_identity)
            image_name = f"{bid}.png"

            key_stimuli[bid] = {
                "canonical_identity": MC.canonical(spec.final_identity),
                "base_identity": MC.canonical(spec.base_identity),
                "document": spec.document_id,
                "document_sha256": spec.document_sha256,
                "page_number": spec.page_number,
                "region_ordinal": spec.region_ordinal,
                "stratum": spec.stratum,
                # A36.2 -- explicit membership, deterministically ordered. NOT a single-frame
                # projection, which could not represent C-and-D without being ambiguous.
                "frames": list(spec.frames),
                "in_c_frame": C_FRAME in spec.frames,
                "in_d_frame": D_FRAME in spec.frames,
                # A36.4 -- routes are metadata about the stimulus; the answers live apart.
                "adjudication_routes": list(spec.adjudication_routes),
                "human_answer_purposes": list(spec.human_answer_purposes),
                "n_human_tasks": spec.n_human_tasks,
                "is_c_audit_selected": spec.is_c_audit_selected,
                "control_kind": spec.control_kind,
                "control_variant": spec.control_variant,
                "is_r1_repeat": spec.is_r1_repeat,
                "r1_base_identity": MC.canonical(spec.base_identity) if spec.is_r1_repeat else None,
                "presentation_rank": rank,
                "renderer": RENDERER_NAME,
                "renderer_version": str(pymupdf.version),
                "dpi": spec.dpi,
                "bbox_pdf_points": [float(v) for v in bbox],
                "image_width_px": width,
                "image_height_px": height,
                "image": image_name,
                "png_sha256": hashlib.sha256(png).hexdigest(),
                # A35.2 -- the printed-line index the adjudicator reports maps through this
                "region_line_bijection": bijection,
                # A38.6 -- the occurrence-level scoring join, from the COMMITTED FRAME and
                # from nowhere else. There is no caller-provided alternative to disagree with.
                # None for a control, which has no architecture occurrence to join to.
                "identity_candidates": candidates,
                "architecture_occurrences": occurrences,
                # A40 section 6 -- the private control binding, absent on ordinary stimuli.
                "control_source_fixture_sha256": spec.source_fixture_sha256,
                "control_record_digest": spec.control_record_digest,
                "control_pdf_path": spec.control_pdf_path,
                "control_expected_truth": (
                    json.loads(spec.control_expected_json) if spec.control_expected_json else None
                ),
            }
            blind_items.append({"id": bid, "image": image_name, "question": question})
            result.images[image_name] = png
    finally:
        for handle in open_docs.values():
            handle.close()

    # A28.4 -- every R1 repeat shares its primary's committed bbox; only the scale differs.
    _assert_r1_matches_primary(key_stimuli)

    result.key = {
        "schema": "oracle_key/3",
        # A38 repair -- DERIVED from the realized sources, never a hardcoded label. The fixed
        # "DEVELOPMENT + SYNTHETIC -- pre-execution" string would have travelled unchanged onto
        # a confirmatory key and misdescribed the very population the artifact was built from.
        "population": realized_population(documents),
        "execution_boundary_state": execution_boundary_state(),
        "prompt_sha256": hashlib.sha256(question.encode()).hexdigest(),
        "n_stimuli": len(key_stimuli),
        # A48 -- the realized census and the ONE predicate derived from it, recorded so a
        # consumer reads the builder's own answer instead of re-deriving "required" from raw
        # frame membership. `score_metrics` reads these; nothing recomputes the budget.
        "d_frame_census": d_census,
        "d_decision_route_required": d_required,
        "d_decision_budget_owner": "methodology_contracts.d_decision_route_required (A27.3 / A48)",
        "frame_counts": frame_counts(key_stimuli),
        "stimuli": key_stimuli,
    }
    result.blind = {
        "schema": "oracle_blind/1",
        "n_items": len(blind_items),
        "items": blind_items,
    }
    return result


def frame_counts(key_stimuli: dict) -> dict:
    """A36.3 -- raw C, raw D and the overlap, reported SEPARATELY.

    `|C union D|` is never substituted for either frame's denominator. Publishing the overlap
    beside both sizes is what lets a reader see that a C-and-D region is counted once in each
    estimand, rather than having to infer it from a single pooled total.
    """
    primaries = [r for r in key_stimuli.values() if not r["is_r1_repeat"] and r["control_kind"] is None]
    c = [r for r in primaries if r["in_c_frame"]]
    d = [r for r in primaries if r["in_d_frame"]]
    both = [r for r in primaries if r["in_c_frame"] and r["in_d_frame"]]
    return {
        "c_frame": len(c),
        "d_frame": len(d),
        "c_and_d_overlap": len(both),
        "union_reported_for_information_only": len({id(r) for r in c + d}),
        "c_audit_selected": sum(1 for r in primaries if r["is_c_audit_selected"]),
        "human_tasks": sum(r["n_human_tasks"] for r in key_stimuli.values()),
        "ai_route": sum(1 for r in key_stimuli.values() if ROUTE_AI in r["adjudication_routes"]),
        "human_route": sum(1 for r in key_stimuli.values() if ROUTE_HUMAN in r["adjudication_routes"]),
    }


def _assert_r1_matches_primary(key_stimuli: dict) -> None:
    """The repeat must differ from its primary by raster scale and NOTHING else."""
    by_base = {}
    for record in key_stimuli.values():
        by_base.setdefault(record["base_identity"], []).append(record)
    for base, records in by_base.items():
        primary = next((r for r in records if not r["is_r1_repeat"]), None)
        repeat = next((r for r in records if r["is_r1_repeat"]), None)
        if primary is None or repeat is None:
            continue
        if primary["bbox_pdf_points"] != repeat["bbox_pdf_points"]:
            raise OracleBuildError(R1_BBOX_DIFFERS_FROM_PRIMARY, {"base": base})
        if primary["dpi"] != MC.PRIMARY_DPI or repeat["dpi"] != MC.R1_REPEAT_DPI:
            raise OracleBuildError(WRONG_DPI_FOR_STIMULUS, {"primary_dpi": primary["dpi"], "repeat_dpi": repeat["dpi"]})


JOIN_IMAGE_MISSING = "JOIN_IMAGE_MISSING"
JOIN_PNG_SHA_MISMATCH = "JOIN_PNG_SHA_MISMATCH"
JOIN_BLIND_ID_UNKNOWN = "JOIN_BLIND_ID_UNKNOWN"
JOIN_FIELD_MISSING = "JOIN_FIELD_MISSING"

# What a downstream join needs from the private key to bind one adjudication to one region.
REQUIRED_JOIN_FIELDS = (
    "canonical_identity",
    "document_sha256",
    "page_number",
    "region_ordinal",
    "frames",
    "adjudication_routes",
    "stratum",
    "dpi",
    "bbox_pdf_points",
    "png_sha256",
    "region_line_bijection",
    # A38.6 -- the facts the OCCURRENCE-LEVEL scoring join actually needs. Without these the
    # join proved only that a blind id stayed bound to its image, which A35 mistook for
    # sufficiency: a scorer could not have resolved a single adjudicated heading from it.
    "identity_candidates",
    "architecture_occurrences",
)

#: A40 section 6 -- what a CONTROL join needs instead. `identity_candidates` and
#: `architecture_occurrences` are deliberately absent rather than stubbed: a control has no
#: architecture occurrence, and inventing one would fabricate the evidence it exists to bound.
#: Everything that binds the control to its fixture and its private truth IS required.
CONTROL_JOIN_FIELDS = (
    "canonical_identity",
    "control_kind",
    "control_variant",
    "control_source_fixture_sha256",
    "control_record_digest",
    "control_pdf_path",
    "control_expected_truth",
    "adjudication_routes",
    "dpi",
    "bbox_pdf_points",
    "png_sha256",
    "region_line_bijection",
)
JOIN_CONTROL_TRUTH_MISMATCH = "JOIN_CONTROL_TRUTH_MISMATCH"

#: Fields of a control manifest record that the private binding is taken over. Deliberately the
#: TRUTH-BEARING ones: a digest over the whole record would change whenever an unrelated
#: descriptive field moved, and a gate nobody can keep green stops being read.
CONTROL_DIGEST_FIELDS = (
    "canonical_identity",
    "control_kind",
    "variant",
    "source_sha256",
    "generated_sha256",
    "expected_adjudicated_headings",
    "expected_before",
    "expected_after",
    "mutation_recipe",
    "region_bbox_pdf_points",
)


def control_record_digest(record: dict) -> str:
    """A stable digest of one control manifest record's truth-bearing fields."""
    payload = {k: record.get(k) for k in CONTROL_DIGEST_FIELDS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def control_oracle_input_digest(manifest: dict, prompt_path: Path = PROMPT_PATH) -> str:
    """A40.16 -- the ONE authoritative digest of everything the 20-control oracle claim rests on.

    THE PROBLEM THIS SOLVES. `x26` proves the controls traverse the real oracle path, and G6
    requires that evidence -- but an evidence artifact that only reports its own verdict binds to
    nothing. Measured: a `x26` result whose every certified value was replaced with garbage still
    left G6 green, because `failures: []` was all G6 read. A stale PASS produced for a DIFFERENT
    valid control state would have been accepted exactly the same way.

    So both sides compute this from CURRENT inputs and G6 compares: `x26` records it beside its
    verdict, and G6 recomputes it from the manifest and prompt on disk. Any change to a control's
    identity, kind, variant, source or generated SHA, expected truth, mutation recipe or committed
    region changes a record digest; any change to the adjudicator prompt or the frozen route
    vocabulary changes the tail. The rendered PNGs are a deterministic function of those same
    inputs, so binding the inputs binds the renders without G6 having to re-render.
    """
    payload = {
        "schema": "control_oracle_inputs/1",
        "record_digests": sorted(control_record_digest(f) for f in manifest.get("fixtures", [])),
        "prompt_sha256": hashlib.sha256(load_prompt(prompt_path).encode()).hexdigest(),
        "routes": list(ROUTE_ORDER),
        "control_join_fields": list(CONTROL_JOIN_FIELDS),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def verify_join(result: BuildResult, control_manifest: dict | None = None) -> list[dict]:
    """I7 -- is every blind id still bound to the image and region the key says it is?

    Returns the list of defects, empty when the join is sound. This is what makes the key
    LOAD-BEARING: `x21` misassociates the key and requires this to report defects, because a
    key that can be shuffled without detection is not a join, it is decoration.
    """
    defects = []
    for item in result.blind.get("items", []):
        bid = item["id"]
        record = result.key["stimuli"].get(bid)
        if record is None:
            defects.append({"reason": JOIN_BLIND_ID_UNKNOWN, "blind_id": bid})
            continue
        is_control = record.get("control_kind") is not None
        for fname in CONTROL_JOIN_FIELDS if is_control else REQUIRED_JOIN_FIELDS:
            if record.get(fname) in (None, ""):
                defects.append({"reason": JOIN_FIELD_MISSING, "blind_id": bid, "field": fname})
        if is_control and control_manifest is not None:
            # The binding is re-derived from the MANIFEST by canonical identity, so shuffling
            # private truth or fixture identity between two otherwise-valid control blind ids is
            # a reported defect rather than an invisible swap.
            expected = next(
                (
                    f
                    for f in control_manifest.get("fixtures", [])
                    if f.get("canonical_identity") == record.get("canonical_identity")
                ),
                None,
            )
            if expected is None:
                defects.append({"reason": JOIN_CONTROL_TRUTH_MISMATCH, "blind_id": bid, "field": "canonical_identity"})
            else:
                for kf, mf in (
                    ("control_record_digest", None),
                    ("control_expected_truth", "expected_adjudicated_headings"),
                    ("control_source_fixture_sha256", "source_sha256"),
                    ("control_variant", "variant"),
                    ("control_kind", "control_kind"),
                ):
                    want = control_record_digest(expected) if mf is None else expected.get(mf)
                    if record.get(kf) != want:
                        defects.append({"reason": JOIN_CONTROL_TRUTH_MISMATCH, "blind_id": bid, "field": kf})
        png = result.images.get(item["image"])
        if png is None:
            defects.append({"reason": JOIN_IMAGE_MISSING, "blind_id": bid, "image": item["image"]})
            continue
        if record.get("image") != item["image"] or hashlib.sha256(png).hexdigest() != record.get("png_sha256"):
            defects.append({"reason": JOIN_PNG_SHA_MISMATCH, "blind_id": bid, "image": item["image"]})
    return defects


# ------------------------------------------- A38.7 the occurrence-level scoring join

ADJUDICATED_SCHEMA = "oracle_adjudicated/1"
#: Fields of one adjudicated heading. EXACTLY the six `adjudicator_prompt.md` asks for.
ADJUDICATED_HEADING_FIELDS = ("text", "role", "parent", "start_physical_line", "start_x_px")
UNREADABLE = "UNREADABLE"


def _jsonable_key(value):
    """Tuples -> lists, recursively. One serialized form for an occurrence key.

    Both sides of the M1-M5 join are compared as committed JSON; a tuple on one side and a
    list on the other are unequal in Python while being the same key, which would silently
    make every matched-heading denominator zero.
    """
    if isinstance(value, (list, tuple)):
        return [_jsonable_key(v) for v in value]
    return value


def resolve_adjudicated_occurrence(key_record: dict, heading: dict) -> dict:
    """A38.7 -- one adjudicated heading -> its A30 occurrence key, from COMMITTED facts only.

        start_physical_line -> region_line_bijection      -> committed neutral line
        start_x_px          -> oracle_geometry.pixel_to_pdf_x  (A34-aware)
        identity_candidates -> nearest neutral glyph, NO TOLERANCE
        resolved ngid       -> anchor_provenance.occurrence_key(...)

    The superseded linear `anchor_provenance.image_x_to_pdf_x` is deliberately NOT used: `x20`
    measured that MuPDF does not map a clip linearly across the bbox, and A33/A34 replaced it.

    No candidate -> UNMATCHED. Exact tie -> UNMATCHED. No text, kind or order fallback. A
    refusal is REPORTED, never converted into an incorrect match.
    """
    line_key = resolve_start_line(key_record["region_line_bijection"], heading["start_physical_line"])
    candidates = key_record["identity_candidates"].get(f"{line_key[0]}:{line_key[1]}")
    if not candidates:
        return {"matched": False, "reason": MISSING_IDENTITY_CANDIDATES, "neutral_line_key": list(line_key)}

    target_pdf_x = OG.pixel_to_pdf_x(heading["start_x_px"], key_record["bbox_pdf_points"][0], key_record["dpi"])
    ngid, reason = AP.resolve_oracle_start_ngid([(int(c[0]), float(c[1])) for c in candidates], target_pdf_x)
    if ngid is None:
        return {
            "matched": False,
            "reason": reason or OCCURRENCE_UNMATCHED,
            "neutral_line_key": list(line_key),
            "target_pdf_x": target_pdf_x,
        }
    return {
        "matched": True,
        "neutral_line_key": list(line_key),
        "target_pdf_x": target_pdf_x,
        "start_ngid": ngid,
        # JSON-normalised: the emitted side is serialized through the frame, so an occurrence
        # key carrying a tuple here would compare unequal to an identical key carrying a list
        "occurrence_key": _jsonable_key(
            AP.occurrence_key(key_record["document_sha256"], key_record["page_number"], line_key, ngid)
        ),
    }


def validate_adjudicated(adjudicated: dict, key: dict) -> None:
    """A38.7 -- the committed adjudicated artifact's encoding. Refuses; never repairs.

    Fields correspond exactly to `adjudicator_prompt.md`; `UNREADABLE` stays representable per
    field; `notes` may never alter a field, so nothing here reads them; an answer's `id` must
    equal its namespace key; an unknown blind id refuses; and a stimulus missing an answer on a
    route it REQUIRES refuses -- there is no fallback to the other namespace.
    """
    validate_adjudication_namespacing(adjudicated)
    for namespace in ADJUDICATION_NAMESPACES:
        for bid, answer in adjudicated[namespace].items():
            record = key["stimuli"].get(bid)
            if record is None:
                raise OracleBuildError(UNKNOWN_BLIND_ID, {"namespace": namespace, "blind_id": bid})
            if answer.get("id") != bid:
                raise OracleBuildError(ADJUDICATION_ID_MISMATCH, {"key": bid, "id": answer.get("id")})
            for heading in answer.get("headings", []):
                missing = [f for f in ADJUDICATED_HEADING_FIELDS if f not in heading]
                if missing:
                    raise OracleBuildError(ADJUDICATION_ROUTE_MISSING, {"blind_id": bid, "missing_fields": missing})
    # A54 -- EFFECTIVE routes, not the bytes the builder happened to store. For a pre-A48 key
    # the stored `adjudication_routes` were derived from raw frame membership, so honouring them
    # literally would require a human answer on a route A27.3 has already made non-decision-
    # bearing. `effective_record_routes` is the single owner both this and `score_metrics` ask.
    reinterpret = is_pre_a48_frozen_key(key)
    d_required = effective_d_decision_required(key)
    for bid, record in key["stimuli"].items():
        for route in effective_record_routes(record, d_required, reinterpret=reinterpret):
            if bid not in adjudicated.get(route, {}):
                raise OracleBuildError(ADJUDICATION_ROUTE_MISSING, {"blind_id": bid, "route": route})


def write_artifacts(result: BuildResult, out_dir: Path, *, key_name="oracle_key.json", blind_name="oracle_blind.json"):
    """Write both artifacts and the images. Refuses to write the CONFIRMATORY paths early."""
    out_dir = Path(out_dir)
    key_path, blind_path = out_dir / key_name, out_dir / blind_name
    assert_write_permitted(key_path)
    assert_write_permitted(blind_path)

    report = leakage_report(result.blind, result.key)
    if report["unexpected_keys"] or report["leaked_values"] or report["forbidden_text"]:
        raise OracleBuildError(BLIND_ARTIFACT_LEAKS, report)

    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    for name, png in result.images.items():
        (out_dir / "images" / name).write_bytes(png)
    key_path.write_text(json.dumps(result.key, indent=1, sort_keys=True))
    blind_path.write_text(json.dumps(result.blind, indent=1, sort_keys=True))
    return key_path, blind_path
