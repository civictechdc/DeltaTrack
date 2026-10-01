"""Guardrails on the triggers of the workflows that own a required status check.

Three properties are pinned here: that the test suite runs when a commit lands on the
integration branch (#412), that both required checks answer the merge_group event so
a merge queue can complete a merge (#416), and that every module carrying a @slow test
is named by some workflow, since the marker alone makes a gate runnable but never run.


Nothing ran the test suite when a commit landed on ``develop``, so a broken integration
branch went unreported: ``ci.yml`` fired on ``pull_request`` and on pushes to ``main``
only, while ``security.yml`` did cover ``develop`` on push. The branch therefore showed
green marks after a merge that had left the suite uncollectable, because the workflows
producing those marks never run the tests.

The fix is two words in a YAML list, which is exactly why it is worth pinning: it can be
undone by a reformat, a merge resolution, or a well-meant "align the triggers" edit, and
nothing else in the repository would notice. The comment block in ``ci.yml`` argues both
decisions at length; prose that no test enforces reads as settled while being ungated.

These are deliberately narrow. They assert the two properties #412 turns on and say
nothing about the rest of the workflow, so adding a job or a step does not touch them.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "ci.yml"

# The workflows that own a REQUIRED status check on `develop`, by the context name branch
# protection asks for: `test` from ci.yml, `pip-audit (production deps)` from
# security.yml. A merge queue waits on every required context, so each of these has to
# answer the merge_group event or the queue never completes a merge (#416).
REQUIRED_CHECK_WORKFLOWS = {"ci.yml": "test", "security.yml": "pip-audit (production deps)"}


def _triggers(path: Path) -> dict:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML resolves a bare `on:` key to the boolean True (the YAML 1.1 truthy set),
    # not the string "on". Both spellings are looked up so this does not silently find
    # nothing -- an empty read here would pass the assertions below vacuously if it
    # returned an empty dict instead of raising.
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, f"no trigger block parsed from {path}"
    return triggers


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _push_branches(workflow: dict) -> list[str]:
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, f"no trigger block parsed from {WORKFLOW}"
    return triggers["push"]["branches"]


def _security_push_failures(path: Path) -> list[str]:
    """Why `path` does not run on pushes to `main`, as failures; empty when it does.

    Single validation path for the security-on-main contract the release runbook
    describes (#547): the live guard asserts this is empty for the real security.yml,
    and the negative control asserts it fires for a deliberately mainless workflow, so
    the only way both stay green is for the verdict the runbook depends on to hold. A
    rewrite that accepts a mainless trigger, or that stops reading `push`, flips both.
    """
    triggers = _triggers(path)
    branches = triggers.get("push", {}).get("branches", [])
    if "main" not in branches:
        return [f"push branches are {branches!r}, without 'main'"]
    return []


@pytest.mark.parametrize(("filename", "context"), sorted(REQUIRED_CHECK_WORKFLOWS.items()))
def test_required_checks_report_to_a_merge_queue(filename: str, context: str) -> None:
    """Every required check answers `merge_group`, or a queue stalls on all merges.

    This is inert while no merge queue is enabled, since the event never fires. It is
    pinned anyway because the failure it prevents is both severe and silent: a queue
    waits on a context that is never reported, so nothing merges, and the workflow files
    look correct. Dropping the trigger from either workflow is what would cause it.
    """
    triggers = _triggers(WORKFLOWS / filename)
    assert "merge_group" in triggers, (
        f"{filename} no longer answers the merge_group event, so the required "
        f"'{context}' check would never report for a queued pull request and the merge "
        "queue would stall on every merge -- see #416."
    )


def test_required_test_context_is_an_aggregator_over_all_jobs() -> None:
    """The required `test` context must survive any job rename or resize.

    The workflow now has multiple independent jobs (two non-matrix: `lint-format`
    and `cwd-independence`; six matrix jobs: `fast-tests`, `browser-tests`,
    `external-validation`, `corpus-gates`, `packaging-gate`, `remaining-slow`).
    Each matrix job reports one context per leg (e.g., `fast-tests (3.12)`). The
    aggregator job named exactly `test` needs ALL of them, runs unconditionally,
    and fails unless every job succeeded.

    `cwd-independence` is non-matrix: it varies the working directory, not the
    interpreter.

    Each pinned property fails silently without the other: without `if: always()`
    a skipped dependency skips the aggregator too (no verdict at all), and without
    the explicit result comparison a skipped or cancelled job counts as a pass,
    making the aggregator a rubber stamp.

    Reading each result, comparing against `success`, and exiting non-zero are
    pinned as one CONNECTED contract, all three read from the single step that
    holds the gate. Checked independently they are three textual facts that a
    workflow gating nothing can still exhibit: a comparison whose branch only
    echoes, next to an unreachable `exit 1`, satisfies "compares" and "exits"
    while every dependency failure passes. And carriers must come from the
    gating step's own `env:`, since a step-level binding does not exist in an
    earlier step, so a correct binding added later cannot vouch for a misrouted
    one in the gate.

    That contract is load-bearing for `fail-fast: false` on the matrix jobs:
    they may run every leg to completion, and the aggregator stays non-permissive
    only because it fails whenever any job (or any leg of a matrix job) is not
    `success`.
    """
    jobs = _workflow()["jobs"]
    aggregator = jobs.get("test")
    assert aggregator is not None, (
        "ci.yml has no job named exactly 'test': the required check context stops "
        "reporting the next time a job is renamed or resized."
    )
    needs = aggregator.get("needs")
    needs = [needs] if isinstance(needs, str) else list(needs or [])
    # The aggregator must need all 8 jobs: 2 non-matrix + 6 matrix
    expected_jobs = {
        "lint-format",
        "cwd-independence",
        "fast-tests",
        "browser-tests",
        "external-validation",
        "corpus-gates",
        "packaging-gate",
        "remaining-slow",
    }
    assert set(needs) == expected_jobs, (
        f"the 'test' aggregator must need exactly {sorted(expected_jobs)}, got: {sorted(needs)}"
    )
    # Every needed job must exist
    for job_name in needs:
        assert job_name in jobs, f"aggregator needs missing job '{job_name}'"
    assert aggregator.get("if") == "always()", (
        "the 'test' aggregator must run even when a dependency fails or is skipped, "
        "or a skipped job leaves the required check with no verdict"
    )
    # Everything below is read from ONE step: the one holding the `for result in` gate.
    # A step-level `env:` is scoped to its own step, so a binding in a LATER step does not
    # exist while the loop runs. Merging the aggregator's steps into a single dictionary
    # would let a correct-looking binding in a diagnostic step stand in for a misrouted one
    # in the gate, and this guard would then confirm a contract that never holds at runtime.
    # Job-level `env:` IS in scope for the step, so it is merged underneath the step's own
    # bindings rather than ignored.
    gating_steps = [
        step
        for step in aggregator.get("steps", [])
        if isinstance(step.get("run"), str) and "for result in" in step["run"]
    ]
    assert len(gating_steps) == 1, (
        f"expected exactly one step of the 'test' aggregator to hold the `for result in` gate, "
        f"found {len(gating_steps)}. The results, the comparison and the exit are only connected "
        "within a single step, so a gate split across steps is one this guard cannot testify about."
    )
    gate = gating_steps[0]
    normalised = " ".join(str(gate["run"]).split())
    scoped_env = {
        name: str(value) for name, value in {**(aggregator.get("env") or {}), **(gate.get("env") or {})}.items()
    }
    # The three properties are pinned as ONE contract, because each is separately satisfiable
    # by a workflow that gates nothing. A loop that compares `$result` against "success" and
    # only echoes, plus an unreachable `if false; then exit 1; fi` further down, contains a
    # comparison AND an `exit 1` while a failed dependency sails through. So the loop body is
    # read out of the loop, and the exit is read out of the branch the comparison takes.
    #
    # Narrow to the shell shape this workflow uses rather than parsing shell. A rewrite into
    # a different shape fails here, which is the right outcome: the guard would no longer be
    # able to say where the exit sits relative to the comparison.
    gate_match = re.search(r"for result in\s+(.*?);\s*do\s+(.*?)\s+done\b", normalised)
    assert gate_match, (
        "the 'test' aggregator has no `for result in ... ; do ... done` gate, so which job "
        "results are compared, and what happens when one is not 'success', cannot be "
        "established from its script"
    )
    result_list, loop_body = gate_match.group(1), gate_match.group(2)
    # Reading a job's result is not the same as ACTING on it. The value has to reach the
    # loop's input list: an env binding alone does not, so a job bound to a variable the list
    # omits is an aggregator that rubber-stamps exactly that one job while every other job
    # still gates. That is the hardest version of this defect to spot in a diff, because the
    # binding is sitting right there looking correct.
    #
    # The list is searched on its own rather than the whole script, which also contains
    # `"$result"` and the error `echo`. Matching a variable anywhere in the script would count
    # a name appearing only in a diagnostic message as though it gated the run.
    #
    # Deliberately NO naming convention is pinned. A job's carrier is found by which binding
    # holds `needs.<job>.result`, not by being spelled FAST_TESTS_RESULT, so renaming the
    # variables is not a failure while misrouting one is. A direct `${{ needs.<job>.result }}`
    # written into the list counts too.
    looped_names = set(re.findall(r"\$\{?(\w+)\}?", result_list))
    for job_name in needs:
        result_expr = f"needs.{job_name}.result"
        carriers = {name for name, value in scoped_env.items() if result_expr in value}
        assert result_expr in result_list or carriers & looped_names, (
            f"the 'test' aggregator never compares {result_expr}. Within the gating step it is "
            f"carried by {sorted(carriers) or 'no env binding'}, and the `for result in` list "
            f"visits {sorted(looped_names)}. The binding is decorative: '{job_name}' could fail "
            "and the required check would still report success."
        )
    branch_match = re.search(r'if\s+\[\s+"\$result"\s+!=\s+"success"\s+\]\s*;\s*then\s+(.*?)\s+fi\b', loop_body)
    assert branch_match, (
        "the 'test' aggregator's loop body does not test `$result` for non-success in the shape "
        f'`if [ "$result" != "success" ]; then ... fi`. The body read was: {loop_body!r}'
    )
    non_success_branch = branch_match.group(1)
    assert re.search(r"\bexit\s+[1-9]\d*\b", non_success_branch), (
        "the 'test' aggregator compares each result against 'success', but the branch taken for "
        f"a non-success result never exits non-zero. That branch is: {non_success_branch!r}. An "
        "`exit 1` elsewhere in the script gates nothing, so the comparison is decorative and the "
        "required check reports success while a dependency failed."
    )


def test_ci_matrix_jobs_do_not_cancel_sibling_legs() -> None:
    """One leg's failure must not erase the other leg's verdict.

    Each matrix leg tests a different supported interpreter, so their verdicts are not
    interchangeable -- WHICH leg broke is most of the diagnosis. Under the default
    ``fail-fast: true`` a failure in any one cancels the rest mid-run, and the cancelled
    legs report nothing: a floor-only regression hides behind an unrelated flake on a newer
    version, a 3.14-only regression hides behind a flake on the floor, and the reviewer sees
    a wall of red from one cause.

    This is the same rule ``test_ci_does_not_cancel_in_progress_runs`` enforces one level up,
    for the same reason: never destroy a verdict. It does **not** make CI permissive. A
    failed leg still fails, and ``test_required_test_context_is_an_aggregator_over_all_jobs``
    separately pins the aggregator that demands success from every job (and every leg).
    """
    workflow = _workflow()
    matrix_job_names = [
        "fast-tests",
        "browser-tests",
        "external-validation",
        "corpus-gates",
        "packaging-gate",
        "remaining-slow",
    ]
    for job_name in matrix_job_names:
        job = workflow["jobs"][job_name]
        strategy = job.get("strategy", {})
        assert strategy.get("fail-fast") is False, (
            f"ci.yml's '{job_name}' matrix does not set `fail-fast: false`, so one leg's "
            "failure cancels the other before it reports. The cancelled leg's verdict is lost, "
            "which is how a floor-only regression hides behind an unrelated failure on the "
            "newest patch."
        )


def test_ci_matrix_jobs_pin_the_interpreter_they_claim_to_test() -> None:
    """A leg labelled 3.14 must actually run on 3.14.

    Without ``UV_PYTHON``, the matrix is decorative. ``uv sync`` builds the right
    environment and the next bare ``uv run`` decides it does not satisfy
    ``.python-version`` (3.12), removes ``.venv`` and rebuilds it on 3.12 -- so the leg
    reports its own label while testing something else, and reports it GREEN. Measured
    when 3.13/3.14 were added: a "3.14" leg ran the suite on 3.12.12.

    The 3.12 legs cannot show this, because any 3.12 patch satisfies a "3.12" request.
    That is precisely why it needs a test rather than a comment: the failure is invisible
    on exactly the versions that were in the matrix when the hole opened.
    """
    workflow = _workflow()
    matrix_job_names = [
        "fast-tests",
        "browser-tests",
        "external-validation",
        "corpus-gates",
        "packaging-gate",
        "remaining-slow",
    ]
    for job_name in matrix_job_names:
        job = workflow["jobs"][job_name]
        assert job.get("env", {}).get("UV_PYTHON") == "${{ matrix.python-version }}", (
            f"ci.yml's '{job_name}' job no longer pins UV_PYTHON to the matrix version. Every "
            "uv call in the job, including ones made from inside a test, falls back to "
            "`.python-version` -- so every non-3.12 leg silently tests 3.12 and passes."
        )


def test_leaf_jobs_have_no_inter_job_dependencies() -> None:
    """The eight leaf jobs must not depend on each other.

    Issue #364 exists because independent checks were serialized in a single job.
    The parallel architecture requires that each leaf job runs independently --
    only the final `test` aggregator should have `needs:` pointing at them.
    A `needs:` edge between leaf jobs would reintroduce the serialization #364 fixed.

    This guard is deliberately narrow: it does not forbid `needs:` globally (the
    aggregator legitimately uses it). It only forbids `needs:` on the eight leaf jobs.
    """
    workflow = _workflow()
    leaf_jobs = {
        "lint-format",
        "cwd-independence",
        "fast-tests",
        "browser-tests",
        "external-validation",
        "corpus-gates",
        "packaging-gate",
        "remaining-slow",
    }
    for job_name in leaf_jobs:
        job = workflow["jobs"][job_name]
        needs = job.get("needs")
        if needs:
            needs_list = [needs] if isinstance(needs, str) else list(needs)
            assert not needs_list, (
                f"leaf job '{job_name}' must not have `needs:` dependencies (found: {needs_list}). "
                "Only the `test` aggregator should depend on leaf jobs. An inter-leaf dependency "
                "re-serializes independent checks, undoing the parallelism from #364."
            )


def test_ci_runs_on_pushes_to_develop() -> None:
    """The integration branch gets a verdict of its own, not just its pull requests.

    Without this, the first person to discover ``develop`` is broken is whoever cuts the
    next branch from it, and the failure surfaces attached to the wrong change.
    """
    branches = _push_branches(_workflow())
    assert "develop" in branches, (
        f"ci.yml no longer runs on pushes to develop (push branches: {branches}). "
        "A merge into the integration branch would land with no test verdict -- see #412."
    )


def test_ci_still_runs_on_pushes_to_main() -> None:
    """Adding `develop` must not come at the cost of the coverage that already existed."""
    branches = _push_branches(_workflow())
    assert "main" in branches, f"ci.yml no longer runs on pushes to main: {branches}"


def test_security_runs_on_pushes_to_main() -> None:
    """The security workflow fires when a commit lands on `main`.

    docs/release.md step 4 tells a maintainer to watch the post-merge security run on
    `main` as one of the three runs that actually test the promotion merge commit. That
    instruction depends on `main` being in security.yml's `push` branches; removing it
    would silently retire the run the runbook is about to watch. `ci.yml`'s push branches
    were already pinned for both branches; security.yml's were not (#547).
    """
    failures = _security_push_failures(WORKFLOWS / "security.yml")
    assert not failures, (
        f"security.yml no longer runs on pushes to main ({'; '.join(failures)}). "
        "docs/release.md step 4 watches this run after a promotion; without it the "
        "promotion merge commit gets no security verdict on `main`."
    )


def test_security_push_guard_detects_a_mainless_trigger(tmp_path: Path) -> None:
    """A security workflow that dropped `main` must go red, not pass via a vacuous read.

    Runs the same validator the live guard uses, `_security_push_failures`, against a
    deliberately mainless workflow, and proves it fires. If the validator were ever
    rewritten to accept a mainless trigger (or to stop reading `push`), this goes red
    along with the live guard, so the assertion above cannot pass vacuously.
    """
    mainless = tmp_path / "security.yml"
    mainless.write_text(
        "name: security\non:\n  push:\n    branches: [develop]\n",
        encoding="utf-8",
    )
    failures = _security_push_failures(mainless)
    assert failures, "the security validator accepted a workflow that does not run on pushes to main"
    assert "main" in failures[0], "the failure does not name the missing branch"


def test_ci_does_not_cancel_in_progress_runs() -> None:
    """A cancelled run leaves a merge commit with no verdict, which is #412 again.

    ``ci.yml`` has no ``concurrency`` group today and does not need one. If a future
    change adds one -- ``update-examples.yml`` is the nearby example to copy from, and it
    does cancel -- this fails rather than quietly reopening the gap. A group with
    ``cancel-in-progress: false`` is fine and passes.
    """
    workflow = _workflow()
    concurrency = workflow.get("concurrency")
    if concurrency is None:
        return
    if isinstance(concurrency, str):
        return
    assert concurrency.get("cancel-in-progress") is not True, (
        "ci.yml cancels in-progress runs. On develop that means a merge commit finishes "
        "with no verdict, recreating the gap #412 closed. Use cancel-in-progress: false."
    )


# --- Every @slow module is named by a workflow ---------------------------------
# Every slow step in ci.yml selects modules by PATH, not by marker: the fast step
# deselects `-m slow`, and each slow step lists the files it runs. So a new @slow module
# is collected by no step at all and reports nothing, while the author sees it pass
# locally and CI go green. The failure is silent in the worst direction -- a gate that
# has never run looks identical to a gate that runs and passes.
#
# ci.yml already argues this in prose at three separate steps ("the marker makes a gate
# runnable, naming its module is what makes it RUN"), which is precisely the situation
# this repository treats as under-gated: a convention carried only in comments. It has
# been rediscovered twice, by the packaging gate (#398) and by the XML-withheld recall
# gate (#507 review).
#
# Scanning EVERY workflow, not just ci.yml, is deliberate: test_govinfo_corpus_parity.py
# is excluded from ci.yml on purpose because it fetches live, and runs from
# corpus-parity.yml instead. Being run by any workflow satisfies this.
#
# The coverage test is STRUCTURAL -- the module name must appear in an executable
# `jobs.*.steps[*].run` command -- and not a search of the raw file text, which would
# fail open. ci.yml *mentions* test_govinfo_corpus_parity.py in a comment, precisely to
# explain that it is excluded there. Under a text search, deleting the real invocation
# from corpus-parity.yml would leave this gate green on the strength of that comment,
# retiring a live-fetch gate silently. Parsing the YAML drops comments, so only a step
# that actually runs the module counts. test_a_module_named_only_in_a_comment_is_not_
# covered pins that, because the difference between the two implementations is invisible
# while every module happens to be correctly registered.


def _slow_test_modules() -> list[Path]:
    """Test modules that define at least one @slow test, found by AST, not by grep.

    A text search for "pytest.mark.slow" also matches the many modules that only
    discuss the marker in a docstring or comment (test_corpus_manifest.py explains at
    length why it is deliberately NOT slow). Those are not gaps, and a false positive
    here would be an unfixable failure, so the marker is read from the syntax tree.
    """
    modules = []
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(_is_slow_marker(node) for node in ast.walk(tree)):
            modules.append(path)
    return modules


def _is_slow_marker(node: ast.AST) -> bool:
    """True for the `pytest.mark.slow` attribute chain, wherever it appears.

    This covers all three spellings the suite uses -- a `@pytest.mark.slow` decorator, a
    module-level `pytestmark = pytest.mark.slow`, and a list of marks -- because every
    one of them contains this attribute access. Walking for the access rather than
    matching each shape means a fourth spelling is caught too.
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "slow"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
    )


#: A test module passed as an ARGUMENT: preceded by whitespace or the start of the
#: command, optionally directory-qualified, and ending at whitespace. Matching the bare
#: name anywhere would also match one quoted inside an English sentence -- which
#: corpus-parity.yml really does contain, in the body of the issue its failure step
#: files. That step runs, so restricting to `run:` blocks alone does not exclude it.
_TEST_MODULE_ARGUMENT = re.compile(r"(?:^|\s)(?:[\w./-]*/)?(test_[A-Za-z0-9_]+\.py)(?=\s|$)", re.MULTILINE)

#: A command counts only if it invokes pytest. The issue-filing step above is the reason:
#: it is executable and names a module, but it runs `gh issue create`, so the module it
#: mentions is documentation, not coverage.
_INVOKES_PYTEST = re.compile(r"\bpytest\b")

#: Shell operators that end one logical command and begin the next. A pipe is included so
#: `pytest ... | tail` splits, leaving the modules with the pytest side.
_COMMAND_SEPARATOR = re.compile(r"&&|\|\||;|\|")


def _workflow_files(directory: Path) -> list[Path]:
    """Every workflow in a directory. Both suffixes: GitHub accepts .yml and .yaml."""
    return sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")])


def _strip_shell_comment(line: str) -> str:
    """Drop a trailing shell comment, ignoring a `#` inside quotes.

    YAML parsing removes YAML comments; it does NOT touch a `#` inside a block scalar,
    which is ordinary shell text. So `# uv run pytest tests/test_x.py` survives parsing
    intact and reads exactly like a live invocation -- the way a gate gets retired
    without the guard noticing.
    """
    quote = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _logical_commands(block: str) -> list[str]:
    """Split a `run:` block into the individual commands a shell would execute.

    Splitting is what ties a module argument to the command that actually runs it.
    Searching the block as one string cannot: it only knows that `pytest` and some
    module name both occur somewhere inside, which is equally true of a block whose
    pytest line is commented out and whose live line runs something else.

    A backslash continuation is joined first, so an invocation wrapped over several
    lines stays one command. A YAML folded scalar (`run: >`, which every slow step in
    ci.yml uses) has already been joined into one line by the parser.
    """
    joined = block.replace("\\\n", " ")
    commands: list[str] = []
    for raw_line in joined.splitlines():
        line = _strip_shell_comment(raw_line)
        if line.strip():
            commands.extend(_COMMAND_SEPARATOR.split(line))
    return commands


def _modules_run_by_workflows(directory: Path) -> set[str]:
    """Test module filenames that some workflow step actually EXECUTES.

    Four filters, each removing a way a name can appear without being run:

    1. Only ``jobs.<id>.steps[*].run`` is read, so a YAML comment, a step ``name:``, a
       job id or the workflow's own ``name:`` do not count. Parsing rather than grepping
       is what draws this line -- PyYAML discards YAML comments before this sees them.
    2. The block is split into logical commands, so a module is credited only to the
       command it is an argument of, not to anything else in the same block.
    3. Shell comments are stripped from each line. YAML parsing leaves them untouched
       inside a block scalar, so a commented-out invocation otherwise reads as live.
    4. Within a command, it must invoke pytest and the module must sit in it as an
       argument rather than inside prose.

    None of these are hypothetical. corpus-parity.yml has an executable step that files a
    tracker issue whose body names a test module in a sentence, which motivated 1 and 4;
    2 and 3 came from review of #507, and both are the shape of a gate being disabled
    temporarily and never re-enabled.
    """
    executed: set[str] = set()
    for path in _workflow_files(directory):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job in (workflow.get("jobs") or {}).values():
            for step in (job or {}).get("steps") or []:
                block = (step or {}).get("run")
                if not isinstance(block, str):
                    continue
                for command in _logical_commands(block):
                    if _INVOKES_PYTEST.search(command):
                        executed.update(_TEST_MODULE_ARGUMENT.findall(command))
    return executed


def test_every_slow_module_is_run_by_a_workflow() -> None:
    """A @slow module no workflow step runs reports green by absence, having run nowhere."""
    slow_modules = _slow_test_modules()
    # Fails closed: an AST change or a moved directory that found nothing would
    # otherwise satisfy the assertion below while checking no module at all.
    assert slow_modules, "found no @slow test modules, so this gate asserted nothing"

    executed = _modules_run_by_workflows(WORKFLOWS)
    # The same fail-closed reasoning from the other side: a YAML-shape change that parsed
    # to no run steps would report every module missing, which is loud, but an empty set
    # here is worth naming as the likely cause rather than sending someone to their diff.
    assert executed, "parsed no test modules out of any workflow `run:` step"

    unrun = [path.name for path in slow_modules if path.name not in executed]
    assert not unrun, (
        f"{len(unrun)} module(s) define @slow tests but are run by no workflow step in "
        f".github/workflows, so those tests run nowhere in CI: {unrun}. Add each to a "
        "slow step in ci.yml -- the marker makes the gate runnable, naming the module in "
        "a `run:` command is what makes it run."
    )


def test_a_module_named_only_in_a_comment_is_not_covered(tmp_path: Path) -> None:
    """The guard reads run steps, not file text, so a mention cannot stand in for a run.

    Both directions are asserted. A guard that reported nothing covered would pass the
    negative case while being useless, so the same module is checked to register when it
    IS in a `run:` command. The live instance this protects is
    test_govinfo_corpus_parity.py, which ci.yml names in a comment explaining that it is
    excluded there: a text search would let its real invocation be deleted from
    corpus-parity.yml without going red.
    """
    mentioned_only = tmp_path / "mentioned.yml"
    mentioned_only.write_text(
        "name: run tests/test_example_gate.py\n"
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      # tests/test_example_gate.py is deliberately excluded here\n"
        "      - name: Skip tests/test_example_gate.py for now\n"
        "        run: echo no tests here\n"
        "      - name: File an issue when the corpus drifts\n"
        "        run: |\n"
        "          gh issue create --body 'add the stem to `tests/test_example_gate.py`'\n"
        "      - name: A pytest step that runs something else entirely\n"
        "        run: uv run pytest -m slow tests/test_other_gate.py\n",
        encoding="utf-8",
    )
    covered = _modules_run_by_workflows(tmp_path)
    assert "test_example_gate.py" not in covered, (
        "a module named only in a comment, a step name, the workflow name and the prose "
        f"of an executable non-pytest step counted as covered: {sorted(covered)}. The "
        "guard is textual and fails open."
    )
    # The neighbouring real invocation must still register, or the negative above would
    # also pass on a guard that simply parsed nothing out of this file.
    assert "test_other_gate.py" in covered, (
        f"the one genuine pytest invocation in the file was not detected: {sorted(covered)}"
    )

    (tmp_path / "runs.yaml").write_text(
        "on: [push]\njobs:\n  build:\n    steps:\n      - run: uv run pytest -v -m slow tests/test_example_gate.py\n",
        encoding="utf-8",
    )
    assert "test_example_gate.py" in _modules_run_by_workflows(tmp_path), (
        "a module invoked by a real `run:` step was not detected, so the guard rejects "
        "everything and proves nothing (the .yaml suffix is covered here too)"
    )


def test_a_commented_out_invocation_is_not_covered(tmp_path: Path) -> None:
    """Commenting a gate out must retire it visibly, not silently.

    This is the disable-and-forget path, and it is the one a text search cannot see at
    all: YAML parsing strips YAML comments but leaves a `#` inside a block scalar alone,
    because there it is ordinary shell text. The commented line still contains the word
    pytest and the module name, so a guard that searches the block as one string reads
    it as a live invocation.
    """
    (tmp_path / "disabled.yml").write_text(
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - run: |\n"
        "          # uv run pytest -m slow tests/test_example_gate.py\n"
        '          echo "temporarily disabled"\n',
        encoding="utf-8",
    )
    covered = _modules_run_by_workflows(tmp_path)
    assert "test_example_gate.py" not in covered, (
        "a commented-out pytest invocation counted as coverage, so a slow gate can be "
        f"retired while this guard stays green: {sorted(covered)}"
    )

    # Uncommenting the same line must register, or the assertion above would also hold
    # on a guard that never counts anything inside a `run: |` block.
    (tmp_path / "disabled.yml").write_text(
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - run: |\n"
        "          uv run pytest -m slow tests/test_example_gate.py\n"
        '          echo "back on"\n',
        encoding="utf-8",
    )
    assert "test_example_gate.py" in _modules_run_by_workflows(tmp_path), (
        "the same invocation, uncommented, was not detected -- the guard is not reading "
        "block scalars at all rather than reading their comments correctly"
    )


def test_a_module_named_beside_a_real_invocation_is_not_covered(tmp_path: Path) -> None:
    """A module credited to a neighbour's pytest command is coverage that does not exist.

    The block runs pytest and mentions two modules, but only one is an argument to that
    command; the other sits in an `echo` saying it is disabled. Matching the block as a
    whole cannot tell them apart, so the disabled module inherits its neighbour's
    invocation. Splitting into logical commands is what separates them.

    The echo is deliberately UNQUOTED. Quoting the path puts a `"` immediately before it,
    which the argument pattern already rejects on its own -- so a quoted version of this
    fixture passes under both the whole-block and the per-command implementation, and
    would assert nothing about the change it exists to pin.
    """
    (tmp_path / "mixed.yml").write_text(
        "on: [push]\n"
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - run: |\n"
        "          echo tests/test_example_gate.py is disabled\n"
        "          uv run pytest -m slow tests/test_other_gate.py\n",
        encoding="utf-8",
    )
    covered = _modules_run_by_workflows(tmp_path)
    assert "test_example_gate.py" not in covered, (
        "a module named in an echo inherited the coverage of a real pytest command in "
        f"the same block: {sorted(covered)}"
    )
    assert "test_other_gate.py" in covered, (
        f"the genuinely invoked module in that block was not detected: {sorted(covered)}"
    )


# --- Every weekly failure reaches a person, whatever failed --------------------
# `corpus-parity.yml` is the only workflow that files an issue, and therefore the only
# scheduled workflow with tracker-backed failure reporting. (`security.yml` also runs on a
# Monday cron, 17 minutes earlier, and reports nowhere but the run list.) That report is
# what makes the gate worth having: a red weekly run emails whoever last edited the cron
# expression and nobody else, which the workflow's own comment explains at the reporting
# step. The report was gated on
#
#     if: failure() && steps.parity.outcome == 'failure'
#
# so it also required the CHECK to have failed. A checkout, uv, Python-install or
# `uv sync` failure leaves that step `skipped` -- or unset, when checkout dies before the
# step exists -- so the workflow went red and filed nothing (#680).
#
# That is fail-open in the worse direction. A parity failure is a real signal about
# upstream data that someone would eventually chase; a broken runner is invisible, and
# the gate stops running entirely while every Monday adds another red to an inbox nobody
# owns. Four scheduled runs had passed at the time of filing, so the reporting step had
# never executed in either branch: its correctness was unobserved, not just its coverage.
#
# TWO properties are pinned below and they fail on different mutations, so neither makes
# the other redundant. `_failure_report_failures` reads the CONDITION and answers "does
# every job failure reach the reporting step at all". The script test EXECUTES the step's
# own `run:` text and answers "once there, does it tell the two failures apart". Re-gating
# the condition leaves the script test green; collapsing the two titles leaves the
# condition guard green.

PARITY_WORKFLOW = WORKFLOWS / "corpus-parity.yml"

# The two titles the reporting script chooses between. They must differ: the reuse search
# matches on title, so one shared title would post "Still failing as of the weekly run"
# onto an open corpus-drift report every time the runner broke, misattributing the cause.
_DRIFT_TITLE = "Corpus filenames diverge from govinfo enumeration"
_SETUP_TITLE = "Weekly corpus-parity check could not run"

# A phrase carried by one report body and absent from the other. The titles alone do not
# pin the diagnosis: copying the drift body under the setup title would hand a reader an
# infrastructure failure described as an upstream corpus rename, telling them to re-fetch
# bills that are not the problem, with every title assertion still green.
_DRIFT_PHRASE = "bill filenames on disk that govinfo enumeration no longer produces"
_SETUP_PHRASE = "never reached its assertion"

# The reporting step is located by what it DOES -- `gh issue create` inside an executable
# `run:` block -- never by its `name`. Matching the name would let a rename drop the step
# out of this guard and take the assertion with it, silently, which is the vacuous pass
# `test_failure_report_guard_detects_a_missing_report_step` pins.
_FILES_AN_ISSUE = re.compile(r"\bgh issue create\b")

# Any read of another step's result inside the condition reintroduces #680, whichever step
# and whichever spelling: `outcome` is the result before `continue-on-error` applies and
# `conclusion` the one after, and both read `skipped` for a step that never ran.
_STEP_RESULT_REFERENCE = re.compile(r"steps\.[A-Za-z0-9_-]+\.(?:outcome|conclusion)")


def _issue_reporting_steps(workflow: dict) -> list[dict]:
    return [
        step
        for job in (workflow.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if _FILES_AN_ISSUE.search(str(step.get("run", "")))
    ]


def _failure_report_failures(path: Path) -> list[str]:
    """Why a job failure in `path` would go unreported, as failures; empty when none would.

    One validation path, as `_security_push_failures` above: the live guard asserts this
    is empty for the real corpus-parity.yml, and two negative controls assert it fires --
    one for the exact condition #680 was filed against, one for a workflow whose reporting
    step is gone. A rewrite that accepts either of those flips all three at once, so the
    live assertion cannot go quietly vacuous.
    """
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = _issue_reporting_steps(workflow)
    if not steps:
        return [f"{path.name} has no step running `gh issue create`, so no failure reaches the tracker"]

    failures: list[str] = []
    for step in steps:
        name = step.get("name", "<unnamed step>")
        # An absent `if:` is not a neutral default here: the step would then run only when
        # everything before it SUCCEEDED, reporting nothing ever. It reads below as a
        # condition that never calls failure(), which is exactly what it is.
        condition = str(step.get("if", ""))
        if "failure()" not in condition:
            failures.append(f"{name!r} has if: {condition!r}, which never calls failure()")
        if "always()" in condition:
            failures.append(f"{name!r} has if: {condition!r}; always() also files a report on a green run")
        reference = _STEP_RESULT_REFERENCE.search(condition)
        if reference is not None:
            failures.append(f"{name!r} gates on {reference.group(0)}, so a failure BEFORE that step files nothing")
    return failures


def test_every_job_failure_reaches_the_failure_report() -> None:
    """A failure anywhere in the parity job must reach the tracker, not just a parity failure.

    Protects the reporting contract the workflow argues for in prose: a weekly job's
    failure has to land somewhere a person actually looks. The mutation that turns this
    red is re-adding any `steps.*.outcome` term to the condition, narrowing it to
    `success()`, or widening it to `always()`.
    """
    failures = _failure_report_failures(PARITY_WORKFLOW)
    assert not failures, (
        f"a job failure in {PARITY_WORKFLOW.name} would not be reported ({'; '.join(failures)}). "
        "The infrastructure failures are the ones that persist: the workflow reddens weekly "
        "into an inbox nobody owns and the parity gate stops running -- see #680."
    )


def test_failure_report_guard_detects_a_step_gated_condition(tmp_path: Path) -> None:
    """The exact condition #680 was filed against must be rejected, not merely disliked.

    Runs the live validator against a workflow carrying the pre-fix condition verbatim.
    Without this the live assertion above could pass because the validator stopped reading
    conditions at all, which is indistinguishable from passing because the condition is
    right.
    """
    gated = tmp_path / "corpus-parity.yml"
    gated.write_text(
        "on:\n"
        "  schedule:\n"
        '    - cron: "17 6 * * 1"\n'
        "jobs:\n"
        "  parity:\n"
        "    steps:\n"
        "      - name: Check corpus filenames against live govinfo enumeration\n"
        "        id: parity\n"
        "        run: uv run pytest -m slow --run-network tests/test_govinfo_corpus_parity.py\n"
        "      - name: Report a failure as an issue\n"
        "        if: failure() && steps.parity.outcome == 'failure'\n"
        '        run: gh issue create --title "$title" --body "$body"\n',
        encoding="utf-8",
    )
    failures = _failure_report_failures(gated)
    assert failures, "the validator accepted the exact step-gated condition #680 was filed against"
    assert any("steps.parity.outcome" in failure for failure in failures), (
        f"the failure does not name the step reference that causes the gap: {failures}"
    )


def test_failure_report_guard_detects_a_missing_report_step(tmp_path: Path) -> None:
    """A workflow that files nothing must fail the guard rather than pass it empty-handed.

    The condition checks iterate over the reporting steps found, so a workflow with none
    would satisfy every one of them vacuously and report zero failures. Deleting the
    reporting step is a realistic edit -- it is the one step a "simplify the weekly job"
    change would drop -- and it retires the whole reporting mechanism, which is a strictly
    worse outcome than the gap #680 describes.
    """
    silent = tmp_path / "corpus-parity.yml"
    silent.write_text(
        "on:\n"
        "  schedule:\n"
        '    - cron: "17 6 * * 1"\n'
        "jobs:\n"
        "  parity:\n"
        "    steps:\n"
        "      - name: Check corpus filenames against live govinfo enumeration\n"
        "        id: parity\n"
        "        run: uv run pytest -m slow --run-network tests/test_govinfo_corpus_parity.py\n",
        encoding="utf-8",
    )
    failures = _failure_report_failures(silent)
    assert failures, "the validator passed a workflow with no issue-filing step at all"
    assert "gh issue create" in failures[0], f"the failure does not name what is missing: {failures}"


def _report_script(path: Path) -> str:
    """The reporting step's own `run:` text, exactly as CI would execute it.

    Read out of the parsed workflow rather than copied into this module, so the test
    cannot drift from the script that actually runs. A copy would encode this test's
    belief about the script and stay green while the two diverged.
    """
    steps = _issue_reporting_steps(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert len(steps) == 1, f"expected exactly one issue-filing step in {path.name}, found {len(steps)}"
    return str(steps[0]["run"])


def _run_report_script(tmp_path: Path, parity_outcome: str) -> list[list[str]]:
    """Execute the real reporting script against a stubbed `gh`; return the calls it made."""
    script = tmp_path / "report.sh"
    script.write_text(_report_script(PARITY_WORKFLOW), encoding="utf-8")

    calls = tmp_path / "gh-calls.jsonl"
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    # Records argv and prints nothing. Empty output from `gh issue list` stands for "no
    # open report of this kind", so the script takes its create path. The shebang names
    # this interpreter directly, so the stub does not depend on the stripped PATH below
    # happening to contain a python.
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['GH_CALLS'], 'a') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    # An EMPTY working directory, deliberately. The failure being reported may be the
    # checkout itself, so a reporting path that reads anything out of the repository could
    # not run in precisely the case #680 is about. The environment is stripped to the
    # variables the step declares, for the same reason: `set -u` then turns any reliance
    # on something CI does not provide into a non-zero exit rather than a silent empty
    # substitution.
    workdir = tmp_path / "empty"
    workdir.mkdir()

    completed = subprocess.run(
        ["bash", str(script)],
        cwd=workdir,
        env={
            "PATH": f"{stub_dir}:/usr/bin:/bin",
            "GH_CALLS": str(calls),
            "GH_TOKEN": "stub-token",
            "GITHUB_REPOSITORY": "AgoraDMV/DeltaTrack",
            "RUN_URL": "https://example.invalid/actions/runs/1",
            "PARITY_OUTCOME": parity_outcome,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"the reporting script exited {completed.returncode} for PARITY_OUTCOME={parity_outcome!r}: {completed.stderr}"
    )
    return [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]


def _argument(call: list[str], flag: str) -> str:
    assert flag in call, f"{flag} is not among the arguments of `gh {' '.join(call)}`"
    return call[call.index(flag) + 1]


@pytest.mark.parametrize(
    ("parity_outcome", "expected_title", "expected_phrase", "wrong_phrase"),
    [
        ("failure", _DRIFT_TITLE, _DRIFT_PHRASE, _SETUP_PHRASE),
        ("skipped", _SETUP_TITLE, _SETUP_PHRASE, _DRIFT_PHRASE),
        ("", _SETUP_TITLE, _SETUP_PHRASE, _DRIFT_PHRASE),
    ],
    ids=["check-failed", "check-skipped", "check-never-existed"],
)
def test_failure_report_titles_and_explains_each_condition(
    tmp_path: Path, parity_outcome: str, expected_title: str, expected_phrase: str, wrong_phrase: str
) -> None:
    """Corpus drift and a broken runner file separate reports, and reuse separate reports.

    Two things are pinned, and the title alone is not enough for either.

    The TITLE is what the reuse search matches on, so sharing one would comment "Still
    failing as of the weekly run" onto an open corpus-drift report whenever the runner
    broke, blaming upstream data for an infrastructure fault and leaving the real state
    buried. The mutation is giving both branches the same title.

    The BODY is what a reader acts on, and it can be wrong while the title is right.
    Copying the drift body under the setup title would tell someone whose runner broke to
    re-download bills and edit `_ACCEPTED_EXTRA_STEMS`, none of which is the problem, and
    a title-only assertion stays green throughout. So each case asserts a phrase its own
    body carries AND that the other body's phrase is absent; presence alone would still
    pass if the two bodies were concatenated.

    The three cases are the three values CI can actually produce. `failure` is the check
    itself; `skipped` is a step that failed ahead of it; the empty string is the checkout
    dying before the parity step exists, so `steps.parity` never enters the context. The
    last is the headline case of #680 and is the reason the assertions run from an empty
    directory.
    """
    calls = _run_report_script(tmp_path, parity_outcome)

    assert [call[:2] for call in calls] == [["issue", "list"], ["issue", "create"]], (
        f"expected a reuse search followed by one create, got {calls}"
    )
    search, create = calls

    assert _argument(search, "--search") == f'"{expected_title}" in:title', (
        f"the reuse search for PARITY_OUTCOME={parity_outcome!r} does not look for its own report: {search}"
    )
    assert _argument(create, "--title") == expected_title, (
        f"PARITY_OUTCOME={parity_outcome!r} filed the wrong report title: {create}"
    )
    assert _argument(create, "--repo") == "AgoraDMV/DeltaTrack", f"filed against the wrong repository: {create}"

    body = _argument(create, "--body")
    assert "https://example.invalid/actions/runs/1" in body, (
        f"the report body does not carry the run log link, which is all a reader has: {create}"
    )
    assert expected_phrase in body, (
        f"PARITY_OUTCOME={parity_outcome!r} filed {expected_title!r} without the explanation that "
        f"condition calls for; expected a body saying {expected_phrase!r}, got: {body!r}"
    )
    assert wrong_phrase not in body, (
        f"PARITY_OUTCOME={parity_outcome!r} filed a body describing the OTHER condition "
        f"({wrong_phrase!r}), which sends the reader after the wrong cause: {body!r}"
    )
