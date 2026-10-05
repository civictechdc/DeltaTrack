# Issue analysis template

A fill-in skeleton for issues written from inside the codebase: a defect you found
while working, a gate that can't fail, a contract that's being violated, a piece of
work you've already analyzed.

This is **not** one of the templates in `.github/ISSUE_TEMPLATE/`. Those are for people
filing through the GitHub web UI, and the chooser deliberately stays short. This file is
for the team and for AI assistants filing from the command line, where
`gh issue create --body-file` bypasses the chooser entirely (see "Filing from the command
line" in [CONTRIBUTING.md](../CONTRIBUTING.md)).

Copy the skeleton, fill it, delete what doesn't apply, and file it with an explicit
`--type`. Sections marked optional are genuinely optional. A short issue that follows the
shape beats a long one that doesn't.

## Why this shape

An issue is read by people who weren't there when you found the problem: a teammate
triaging next month, a newcomer picking up their first issue, you in six weeks. Three
failure modes account for most unreadable issues, and the skeleton is built to prevent
each one.

- **Starting from the artifact.** Opening with a test node ID or a function name puts the
  evidence before the problem, so a reader has to reconstruct what's wrong from what you
  looked at. State the observable behavior first.
- **Bare cross-references.** `see #141` makes the number do the explaining, and a reader
  without that tab open loses the thread. Describe every reference inline.
- **Fusing the defect with its discovery.** "What's wrong" and "how I found it" are
  different claims. Merged, they read as lab notes: a story about your afternoon rather
  than a statement about the code.

---

## Skeleton

```markdown
**What's wrong**

<!-- First sentence assumes no knowledge of this codebase: state the observable
     wrong behavior before naming any file, test, or function. Someone without the
     repo open should finish it knowing what's broken and why they'd care.

     Then the supporting detail. Define project terms on first use ("anchors", the
     line-number markers the PDF parser uses to locate where a section starts).
     Describe cross-references inline: #141 (enrolled PDFs yield no anchors), not a
     bare #141. Same for decision records: ADR 0009 (validation ground truth). -->

**How it surfaced**

<!-- What you were doing, and the evidence. Paste real output, not a reconstruction
     of it. Keep it to the lines that matter. -->

**Why it matters**

<!-- Consequence: what breaks, for whom, and how urgently. Say if something is
     currently masking it, and whether pending work would make it worse. Don't
     assume it's obvious; the person triaging this has less context than you. -->

**What to do** <!-- optional -->

<!-- Directions with tradeoffs, or "unknown, needs investigation." Prefer options
     over one prescribed fix unless it's genuinely settled: an issue is where the
     decision gets made, not where it gets announced. Measurements comparing
     options are welcome and beat advocacy. -->

**Verification** <!-- optional -->

<!-- Fill this in when "the suite still passes" is not proof of a fix. For a
     fail-open defect it never is, because it passed before, which is the problem.
     Name the known-bad case the fix must be shown to catch. -->

**Unverified** <!-- optional -->

<!-- Anything you suspect but did not test, labeled plainly as such. Worth writing
     down; not worth presenting as established. -->

Refs #<n>, #<n>
```

## Evidence

An issue is a claim about the state of the code, and someone will act on it.

- **Only state results you actually ran.** Never quote a test result, count, coverage
  number, or benchmark from memory or from what a command "would" print. Run it or say
  it's unrun.
- **Scope results to what you ran them on.** A result belongs to one branch and commit.
  Say which if it could matter.
- **Say what you didn't check.** The boundary of the investigation is information.

## Other genres

The same shape trims down for work that isn't a defect. Keep the first-sentence rule and
self-describing references in all cases; those are what make an issue readable.

| Filing | Use |
|---|---|
| **Defect** | The full skeleton. |
| **Task / chore** | *What's wrong* becomes what needs doing; *why it matters* becomes what it unblocks. Drop the rest. |
| **Feature** | *What's wrong* becomes the problem and who has it; *what to do* becomes the proposed shape. Drop verification. |
| **Epic** | Problem and why, plus the decomposition. Sub-issues carry their own analysis. |

Don't add acceptance criteria, scope, priority, or sizing. Those get set during grooming
(see "Grooming an issue for pickup" in [CONTRIBUTING.md](../CONTRIBUTING.md)), and a filer
who guesses at them creates work to undo.

## Grooming: rewriting an issue for pickup

Grooming turns a filed issue into one a newcomer can start on (see "Grooming an issue
for pickup" in [CONTRIBUTING.md](../CONTRIBUTING.md)). When the body needs more than a
line or two added, rewrite it in this shape. Keep every substantive finding and
decision from the old body; restructure and clarify, don't redesign.

```markdown
**What's wrong**   <!-- or **What needs doing** for a task, **What needs deciding** for a decision -->

<!-- The first-sentence rule still applies: the observable problem, in plain words. -->

Some background, for anyone new to this:

- **<Term>.** <One or two sentences. Only the terms this issue actually needs:
  e.g. the XML vs PDF pipeline, change cards, breadcrumb, anchors, enrolled bill.>

**Example** / **How it shows up** (checked <YYYY-MM-DD> on `develop`, `<short sha>`)

<!-- A real, concrete case: bill, versions, what the report shows vs what's printed.
     Paste output you ran. -->

**Why it matters**

**Cause**   <!-- if known; file:line references belong here, after the plain explanation -->

**Done when**

- [ ] <Observable acceptance criteria. Name the known-bad case the fix must be shown
      to catch, and say "fails on develop" where a passing suite proves nothing.>

**Scope**

- **In scope:** …
- **Out of scope:** … <!-- with the issue number that owns each excluded piece -->

**Where to start**

- <Entry files and functions, the test file to extend, a repro command or snippet,
  docs or ADRs to read. Name any open PR that overlaps, with its state.>

**Unverified**   <!-- optional -->

**History**   <!-- optional: only what helps, e.g. why an earlier number changed -->
```

Conventions that came out of rewriting the backlog in October 2026:

- **Date-stamp and commit-stamp every measurement.** "Checked 2026-10-05 on `develop`
  (`48082b2`)". Older numbers may stay, with their date and source, in History.
- **Check an open PR's base before attributing behaviour to it.** Many PRs sit 100+
  commits behind `develop`. A difference between a PR and `develop` can come from what
  `develop` gained since the PR branched. Run the PR's merge base too, or a trial merge
  in a scratch worktree, before blaming the PR. (A whole "regression" in #738's first
  rewrite turned out to be branch age.)
- **When an open PR fixes the issue, say so in Where to start.** "Don't start a fresh
  fix; help land #NNN", with the PR's review and conflict state.
- **Describe decisions as options with tradeoffs,** and record whose call it is. Once
  the maintainer decides, write the decision and its date into the body.
- **Length.** About 300–700 words for most issues; 200–450 for Low priority; epics can
  run longer for their decomposition table (issue, what it covers, status).
- **No priority or effort in the body.** Those live in the org-level fields.

## Closing or merging an issue

A close needs a comment a stranger can follow later. Two to six sentences:

- **Why**, with evidence: the PR that fixed it, a measurement, or the product decision
  that retired it.
- **The state reason:** `completed` (done), `not_planned` (won't do, or no longer
  applies), or `duplicate` of the issue that absorbs it.
- **For a merge, what carries over.** Put any regression case or acceptance item into
  the surviving issue (edit its body, or comment there), so it isn't lost with the
  closed one.
- **When to reopen,** if there's a clear trigger.
