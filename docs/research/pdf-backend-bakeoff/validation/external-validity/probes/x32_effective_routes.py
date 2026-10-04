"""x32 -- A54. Only the frozen pre-A48 artifact routes by effective requirement.

NOT CONFIRMATORY. SYNTHETIC populations only; opens no holdout document, no real oracle image
and no real key, and writes no canonical artifact. The real-key controls (exception granted,
AI 122 / human 45 accepted) need the 307 MB committed artifact, which does not exist on
develop; they are run against the study worktree and recorded in the A54 deviation instead.

THE BEHAVIOUR THIS PRESERVES
----------------------------
A48 gave A27.3's budget an executable owner but left `key.get("d_decision_route_required",
True)` at the consumers, so a key predating those fields kept meaning what it meant when built.
That is correct for every key but the one frozen confirmatory artifact: its census is 13,992,
A27.3 has already made the D decision route non-decision-bearing, and the default therefore
demands a human answer on all 15,417 stored human routes before ANY metric can be produced.
Not conservative, unsatisfiable.

TWO PROPERTIES, AND THE SECOND IS THE LOAD-BEARING ONE
------------------------------------------------------
  1. For the frozen artifact, routes are derived from purposes and `d_decision` is dropped.
  2. For EVERYTHING ELSE, reinterpretation is OFF and the stored `adjudication_routes` remain
     the requirement, exactly as before A54.

The exception is bound to the artifact's SHA-256 over its complete content, not to a summary
fingerprint. A summary (schema, stimulus count, prompt digest, frame counts) is not an identity:
the whole `stimuli` mapping can change while every summary field holds, and since routes are
derived from `human_answer_purposes`, a key could drop `c_audit` from one selected record, keep
`frame_counts.c_audit_selected == 25`, and validate 24 audit answers while looking complete.

THE MUTATIONS THAT MUST BREAK IT
--------------------------------
  * `effective_d_decision_required` always True -> the D=61 arm goes RED
  * `is_pre_a48_frozen_key` always True         -> the SCOPING arm goes RED
  * reinterpretation forced on for every key    -> the POST-A48 arm goes RED
  * routes dropped for D members                -> the C-audit overlap arm goes RED
  * memoizing identity against the dict object  -> the same-object-mutation arm goes RED
All four are injected into the real functions below, not into copies of their output.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
EV = HERE.parents[1]
BAKE = EV.parents[1]
REPO = BAKE.parents[2]
for _p in (str(HERE.parent), str(REPO / "src"), str(BAKE / "probes"), str(BAKE / "probes" / "backends")):
    sys.path.insert(0, _p)

import build_oracle as BO  # noqa: E402
import control_fixtures as CF  # noqa: E402
import x21_build_oracle as X21  # noqa: E402
import x31_dframe_budget_routes as X31  # noqa: E402

RESULTS: list[dict] = []
SUMMARY_FIELDS = ("schema", "n_stimuli", "prompt_sha256", "frame_counts")


def check(name, ok, why, observed=""):
    RESULTS.append({"check": name, "pass": bool(ok), "expected": why, "observed": str(observed)[:200]})


def build_mixed(n_c_only: int, n_d_only: int, n_both: int, tmp) -> dict:
    """A real BO.build over a synthetic population that CONTAINS C-and-D overlap.

    `X31.build` lays out C-only and D-only pages, so it cannot exercise the overlap the real
    population has (72 overlapping regions, 19 of them C-audit-selected). The C audit is drawn
    from the C frame, so once overlap exists some audit picks are necessarily also D members.
    """
    mem = [(True, False)] * n_c_only + [(True, True)] * n_both + [(False, True)] * n_d_only
    docs = X21.synthetic_documents(tmp, n_pages=len(mem), memberships=mem)
    controls = BO.control_specs(json.loads(CF.MANIFEST_PATH.read_text()), EV, REPO)
    return BO.build(docs, controls=controls).key


def stale(key: dict) -> dict:
    """A synthetic key aged back to the PRE-A48 encoding.

    Drops the two A48 fields and restores the pre-A48 derivation, in which raw D membership alone
    created an unconditional human route and a `d_decision` purpose. This is the shape the frozen
    confirmatory key really has; nothing here is written anywhere.
    """
    out = copy.deepcopy(key)
    out.pop("d_frame_census", None)
    out.pop("d_decision_route_required", None)
    for record in out["stimuli"].values():
        purposes = list(record.get("human_answer_purposes") or ())
        routes = set(record.get("adjudication_routes") or ())
        if record.get("in_d_frame"):
            if BO.PURPOSE_D_DECISION not in purposes:
                purposes.append(BO.PURPOSE_D_DECISION)
            routes.add(BO.ROUTE_HUMAN)
        record["human_answer_purposes"] = purposes
        record["adjudication_routes"] = [r for r in BO.ROUTE_ORDER if r in routes]
    return out


def pin(key: dict) -> None:
    """Treat `key` as the frozen artifact, by its real content digest."""
    BO.PRE_A48_FROZEN_KEY_SHA256 = BO.canonical_sha256(key)


def summary_of(key: dict) -> dict:
    return {f: copy.deepcopy(key.get(f)) for f in SUMMARY_FIELDS}


def routes_of(key: dict, record: dict) -> tuple:
    return BO.effective_record_routes(
        record, BO.effective_d_decision_required(key), reinterpret=BO.is_pre_a48_frozen_key(key)
    )


def effective_human(key: dict) -> set:
    return {b for b, r in key["stimuli"].items() if BO.ROUTE_HUMAN in routes_of(key, r)}


def effective_ai(key: dict) -> set:
    return {b for b, r in key["stimuli"].items() if BO.ROUTE_AI in routes_of(key, r)}


def non_d_human(key: dict) -> set:
    """The C-audit plus control human population, independent of any route byte."""
    return {
        bid for bid, record in key["stimuli"].items()
        if set(record.get("human_answer_purposes") or ()) & {BO.PURPOSE_C_AUDIT, BO.PURPOSE_CONTROL_HUMAN}
    }


def answers(human_ids, ai_ids) -> dict:
    def one(bid):
        return {"id": bid, "headings": [], "notes": []}

    return {BO.ROUTE_AI: {b: one(b) for b in ai_ids}, BO.ROUTE_HUMAN: {b: one(b) for b in human_ids}}


def denies_with_same_summary(name, original, mutated, why):
    """Assert a mutation keeps every summary field yet loses the exception."""
    same = summary_of(original) == summary_of(mutated)
    denied = not BO.is_pre_a48_frozen_key(mutated)
    check(name, same and denied, why,
          f"summary_identical={same} exception_denied={denied}")


def main() -> int:
    original_pin = BO.PRE_A48_FROZEN_KEY_SHA256
    try:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            over_live, under_live = X31.build(61, 30, tmp), X31.build(60, 30, tmp)
            over, under = stale(over_live), stale(under_live)

            check("premise: the synthetic keys really are pre-A48 shaped",
                  "d_decision_route_required" not in over and "d_frame_census" not in over,
                  "the A48 fields are absent, as in the frozen artifact", sorted(over)[:5])

            # ---- SCOPING: an unrecognised key earns nothing ---------------------------
            check("SCOPING: an unpinned pre-A48-shaped key does NOT get the exception",
                  BO.effective_d_decision_required(over) is True,
                  "only the artifact matching the pinned content digest is excepted",
                  BO.effective_d_decision_required(over))

            # ---- IDENTITY: the digest covers every field ------------------------------
            pin(over)
            check("IDENTITY: the exact pinned key receives the exception",
                  BO.is_pre_a48_frozen_key(over), "its canonical digest matches", "")

            irrelevant = copy.deepcopy(over)
            irrelevant["population"] = str(irrelevant.get("population")) + " "
            check("IDENTITY: changing one otherwise irrelevant field DENIES it",
                  not BO.is_pre_a48_frozen_key(irrelevant),
                  "the identity is the whole artifact, not the fields routing happens to read",
                  "")

            audit_ids = [b for b, r in over["stimuli"].items() if r.get("is_c_audit_selected")]
            no_audit = copy.deepcopy(over)
            no_audit["stimuli"][audit_ids[0]]["human_answer_purposes"] = [
                p for p in no_audit["stimuli"][audit_ids[0]]["human_answer_purposes"]
                if p != BO.PURPOSE_C_AUDIT
            ]
            denies_with_same_summary(
                "IDENTITY: removing one C-audit purpose, every summary field unchanged, DENIES it",
                over, no_audit,
                "the summary fingerprint this replaced would have accepted it and validated 24 "
                "audit answers while frame_counts still said 25")

            moved_route = copy.deepcopy(over)
            victim = next(b for b, r in moved_route["stimuli"].items()
                          if BO.ROUTE_HUMAN in (r.get("adjudication_routes") or ()))
            moved_route["stimuli"][victim]["adjudication_routes"] = [BO.ROUTE_AI]
            denies_with_same_summary(
                "IDENTITY: changing one stored route, every summary field unchanged, DENIES it",
                over, moved_route, "stored routes are part of the artifact's identity")

            # ---- A: the frozen artifact narrows to the non-D human population ---------
            pin(over)
            check("A pinned D=61: the D decision route is NOT result-bearing",
                  BO.effective_d_decision_required(over) is False,
                  "census 61 exceeds the 60-region budget, so A27.3 denies the route",
                  BO.effective_d_decision_required(over))
            want, got = non_d_human(over), effective_human(over)
            check("A D=61 with stale raw D routes: effective human population is the C-audit "
                  "plus controls, and nothing else", got == want,
                  "the stored bytes still say human on every D member; the owner does not",
                  f"effective={len(got)} want={len(want)}")
            adj = answers(want, effective_ai(over))
            ok, obs = X31.refuses(lambda: BO.validate_adjudicated(adj, over))
            check("A ...and validate_adjudicated ACCEPTS exactly that population", not ok,
                  "no answer is required on a route A27.3 denied", obs)
            check("A ...and no D-only stimulus is human-required",
                  not any(BO.ROUTE_HUMAN in routes_of(over, r) for r in over["stimuli"].values()
                          if r.get("in_d_frame") and not r.get("in_c_frame")
                          and not r.get("is_c_audit_selected") and r.get("control_kind") is None),
                  "a non-required route is ABSENT, not unevaluable", "")
            ok, obs = X31.refuses(
                lambda: BO.validate_adjudicated(X31.drop(dict(adj, schema="x"), BO.ROUTE_HUMAN, audit_ids[0]), over))
            check("A omitting a C-audit human answer REFUSES", ok,
                  "the C audit stays complete and falsifiable", obs)
            control_ids = [b for b, r in over["stimuli"].items() if r.get("control_kind") is not None]
            check("A all 20 human controls are required",
                  len(control_ids) == 20 and all(b in got for b in control_ids),
                  "8 N-A, 8 N-B, 4 N-C stay human-required", len(control_ids))
            ok, obs = X31.refuses(
                lambda: BO.validate_adjudicated(X31.drop(dict(adj, schema="x"), BO.ROUTE_HUMAN, control_ids[0]), over))
            check("A omitting a human control answer REFUSES", ok,
                  "controls keep the human arm falsifiable", obs)
            check("A AI requirements do not move",
                  effective_ai(over) == {b for b, r in over["stimuli"].items()
                                         if BO.ROUTE_AI in (r.get("adjudication_routes") or ())},
                  "the repair narrows the human route only", len(effective_ai(over)))

            real_d = BO.effective_d_decision_required
            try:
                BO.effective_d_decision_required = lambda key: True
                check("MUTATION always-required: the D=61 arm goes RED",
                      effective_human(over) != want,
                      "forcing the budget True must reintroduce the D human population",
                      len(effective_human(over)))
            finally:
                BO.effective_d_decision_required = real_d

            real_is = BO.is_pre_a48_frozen_key
            try:
                BO.is_pre_a48_frozen_key = lambda key: True
                pin(under)  # pin something else, so only the forced predicate can grant it
                BO.is_pre_a48_frozen_key = lambda key: True
                check("MUTATION unpinned exception: the SCOPING arm goes RED",
                      BO.effective_d_decision_required(over) is not True,
                      "granting on shape alone must let an unrecognised key narrow itself",
                      BO.effective_d_decision_required(over))
            finally:
                BO.is_pre_a48_frozen_key = real_is

            # ---- B: C-and-D audit overlap survives the narrowing ----------------------
            mixed = stale(build_mixed(10, 31, 30, tmp))
            pin(mixed)
            overlap = [b for b, r in mixed["stimuli"].items()
                       if r.get("is_c_audit_selected") and r.get("in_d_frame")]
            mixed_human = effective_human(mixed)
            check("B premise: the mixed population really contains C-and-D audit overlap",
                  bool(overlap), "otherwise the next check passes vacuously on an empty set",
                  f"{len(overlap)} overlapping audit items")
            check("B every C-and-D audit-selected item stays human-required for the C audit",
                  bool(overlap) and all(b in mixed_human for b in overlap),
                  "the C audit is a frozen 25-item instrument; D membership must not shrink it",
                  f"{len(overlap)} overlapping audit items")
            real_routes = BO.effective_record_routes
            try:
                BO.effective_record_routes = lambda record, d, **kw: (
                    () if record.get("in_d_frame") else real_routes(record, d, **kw))
                check("MUTATION drop-overlap: the C-audit overlap arm goes RED",
                      not all(b in effective_human(mixed) for b in overlap),
                      "narrowing that also drops overlapping audit items must be caught", "")
            finally:
                BO.effective_record_routes = real_routes

            # ---- C: POST-A48 semantics are untouched ----------------------------------
            # A post-A48 key whose stored routes require MORE than its purposes do. Before A54
            # the stored route governed; the exception must not make this key easier to validate.
            mismatch = copy.deepcopy(over_live)
            target = next(b for b, r in mismatch["stimuli"].items()
                          if BO.ROUTE_HUMAN not in (r.get("adjudication_routes") or ())
                          and r.get("control_kind") is None)
            mismatch["stimuli"][target]["adjudication_routes"] = [
                r for r in BO.ROUTE_ORDER
                if r in set(mismatch["stimuli"][target].get("adjudication_routes") or ()) | {BO.ROUTE_HUMAN}
            ]
            pin(over)  # the frozen pin is some OTHER artifact, as in production
            check("C premise: the mismatched key is post-A48 and unrecognised",
                  "d_decision_route_required" in mismatch and not BO.is_pre_a48_frozen_key(mismatch),
                  "it carries the A48 fields, so it never reaches the digest", "")
            m_human = {b for b, r in mismatch["stimuli"].items() if BO.ROUTE_HUMAN in routes_of(mismatch, r)}
            m_adj = answers(m_human - {target}, effective_ai(mismatch))
            ok, obs = X31.refuses(lambda: BO.validate_adjudicated(m_adj, mismatch))
            check("C POST-A48: a stored human route with no matching purpose is STILL required",
                  ok, "pre-A54 behaviour preserved; the frozen exception must not excuse a "
                      "stored requirement on a different key", obs)
            real_routes2 = BO.effective_record_routes
            try:
                BO.effective_record_routes = lambda record, d, **kw: real_routes2(record, d, reinterpret=True)
                ok2, obs2 = X31.refuses(lambda: BO.validate_adjudicated(m_adj, mismatch))
                check("MUTATION reinterpret-everything: the POST-A48 arm goes RED", not ok2,
                      "deriving routes for every key must let the malformed key validate", obs2)
            finally:
                BO.effective_record_routes = real_routes2

            # ---- E: identity is RE-READ, never remembered -----------------------------
            # A verdict memoized against a mutable dict is a verdict about the key as it WAS.
            # Holding a strong reference stops the id being recycled onto a different object and
            # stops nothing about this object being edited afterwards, so the mutation below is
            # performed on the SAME dict, with no cache cleared and no copy taken.
            live = stale(X31.build(61, 30, tmp))
            pin(live)
            check("E premise: the key is recognised BEFORE the mutation",
                  BO.is_pre_a48_frozen_key(live), "its canonical digest matches the pin", "")
            before = summary_of(live)
            live_audit = [b for b, r in live["stimuli"].items() if r.get("is_c_audit_selected")]
            victim_rec = live["stimuli"][live_audit[0]]
            victim_rec["human_answer_purposes"] = [
                p for p in victim_rec["human_answer_purposes"] if p != BO.PURPOSE_C_AUDIT
            ]
            check("E the mutation preserves every summary field",
                  summary_of(live) == before and live["frame_counts"]["c_audit_selected"] == 25,
                  "so nothing short of reading the content can catch it",
                  live["frame_counts"]["c_audit_selected"])
            check("E the SAME mutated object is DENIED the exception",
                  not BO.is_pre_a48_frozen_key(live),
                  "identity is recomputed from current content, not recalled from a prior answer",
                  "")
            live_human = effective_human(live)
            still_required = [b for b in live_audit if b in live_human]
            check("E ...and validation does NOT accept a reduced audit population",
                  len(still_required) == len(live_audit),
                  "denied reinterpretation falls back to stored routes, which still require it",
                  f"{len(still_required)} of {len(live_audit)}")
            live_adj = answers(live_human, effective_ai(live))
            ok, obs = X31.refuses(
                lambda: BO.validate_adjudicated(
                    X31.drop(dict(live_adj, schema="x"), BO.ROUTE_HUMAN, live_audit[0]), live))
            check("E ...and dropping that audit answer still REFUSES", ok,
                  "the mutated key cannot validate 24 of 25", obs)

            # ---- D: within budget the complete census is still required ---------------
            pin(under)
            check("D D=60 pinned: the D decision route IS still result-bearing",
                  BO.effective_d_decision_required(under) is True,
                  "a census within budget is exactly where Rule 1 MAY select X",
                  BO.effective_d_decision_required(under))
            u_human = effective_human(under)
            d_members = [b for b, r in under["stimuli"].items()
                         if r.get("in_d_frame") and r.get("control_kind") is None]
            check("D ...and the COMPLETE D census stays human-required",
                  all(b in u_human for b in d_members),
                  "within budget nothing is narrowed", f"{len(d_members)} D members")
            u_adj = answers(u_human, effective_ai(under))
            ok, obs = X31.refuses(
                lambda: BO.validate_adjudicated(X31.drop(dict(u_adj, schema="x"), BO.ROUTE_HUMAN, d_members[0]), under))
            check("D ...and dropping one D human answer REFUSES", ok,
                  "the D route is result-bearing at this census", obs)
    finally:
        BO.PRE_A48_FROZEN_KEY_SHA256 = original_pin

    failed = [r for r in RESULTS if not r["pass"]]
    for r in RESULTS:
        print(f"[{'PASS' if r['pass'] else 'FAIL'}] {r['check']}")
        if not r["pass"]:
            print(f"         expected: {r['expected']}")
            print(f"         observed: {r['observed']}")
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks pass")
    (EV / "results" / "x32_effective_routes.json").write_text(
        json.dumps({"schema": "x32_effective_routes/2", "checks": RESULTS}, indent=1)
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
