# 18. Read appropriations phrases for money, never for structure

- Status: Accepted
- Date: 2026-08-04

## Context

A bill's structure — which account a paragraph belongs to, what its heading says,
where it sits in the hierarchy — has to come from somewhere. Two kinds of signal are
available in a GPO PDF, and they are not equally trustworthy.

The first is **format**: glyph size, position, casing, and the universal tokens every
piece of legislation uses (`TITLE III`, `SEC. 405`, enumerators like `(a)` and `(1)`).
These are the grammar of legislation. Congress does not get to write a bill without
them, and they mean the same thing in an appropriations bill, an authorization, and a
post office naming.

The second is **appropriations vocabulary**: the stock phrases that recur in spending
bills — `For necessary expenses of`, `(INCLUDING TRANSFER OF FUNDS)`, `RESCISSION`.
These are conventions of one bill genre, written by drafters who are free to depart
from them, and they carry no structural guarantee at all. A heading is not an account
because a nearby sentence opens with a particular phrase; it is an account because of
where and how it is set on the page.

Until now the PDF pipeline used both. Account headings were normally found by glyph
size, but when the size signal was not derivable the parser fell back to scanning for
`For necessary expenses of` and walking back up to three lines to whatever uppercase
heading it found, calling that an account. That fallback is what forced the question:
it let an appropriations-specific English phrase name accounts and shape the
hierarchy, on exactly the documents where we had the least other evidence.

Two things make this worse than it sounds. Structure is not a display detail — the
leveled tree is a financial data contract, and a wrong account boundary files money
under the wrong heading. And the failure is silent: a plausible-looking breadcrumb
derived from a guess is indistinguishable, in the output, from one derived from the
page's actual typography.

Measuring the fallback settled what it was worth. The census applied the same
condition the parser uses — the size path runs only when `derive_size_bands` returns
bands **and** glyph-size attachment coverage is at least 0.85, so low-coverage
documents with otherwise valid bands used the fallback too and are counted here.

Across the 68-PDF corpus the fallback ran on 32 files and produced **two** account
anchors in total: one each on two versions of 119-hr-1, a reconciliation bill, and
both were the same wrong reading — the fragment ``EXISTING ``FREE FILE'' PROGRAM AND
ANY ``DIRECT``, which is the middle of a wrapped subsection catchline, labelled
`account`. So the fallback produced one distinct piece of structure across the whole
corpus, and it was incorrect. The other 30 files split into 11 enrolled/public-law
documents the product already declines as unnumbered layouts, and 19
introduced/engrossed shells of 3-14 pages that contain no accounts to find. The
fallback was contributing no correct structure anywhere.

The same phrases are genuinely useful somewhere else. `(increased by $X)`,
`RESCISSION`, `INCLUDING TRANSFER OF FUNDS` say what a dollar change *means*, and
reading that meaning is a planned layer ([#115](https://github.com/AgoraDMV/DeltaTrack/issues/115)).
Interpreting an amount is a much weaker claim than defining a boundary: if the
interpretation is wrong the amount is still right, still attached to the right
account, and the error is visible next to the number it describes.

## Decision

We will treat appropriations-specific English phrases as a **financial-semantics
signal only**. They may interpret dollar amounts. They may not name accounts, create
anchors, or determine hierarchy.

Structure comes from format alone: glyph-size bands, position, and the universal
legislative tokens. When the format signal is absent, the structure **degrades**
rather than substituting a guess. A shallower breadcrumb that is true beats a deeper
one that is invented, because a consumer can see the first is shallow and cannot see
the second is wrong.

Precisely what degrades matters, because it is narrower than "everything below the
title". The **appropriations-specific interior levels** — account, agency, major,
grouping header — are the ones that disappear, because they are exactly what the
glyph-size bands detect. Everything derived from universal legislative grammar
survives: `TITLE N`, `SEC. N`, and the enumerator-derived run-in subsections
(`(a) In general.—`), all of which come from a per-page pass that never consults
size bands. On 119-hr-1 that degrade still yields 11 titles, 355 sections and 936
subsections; what it does not yield is a single account. Structure is thinner, not
absent.

Alternatives considered:

- **Keep the fallback, gated more tightly.** Rejected: the measurement shows it has
  no correct output to preserve, so tightening it optimizes a path whose best
  achievable result is "emits nothing", which is what removing it already does.
- **Retarget the trigger to feed the account↔amount layer instead of the anchor
  list**, as the issue originally proposed. Rejected for now: that layer does not
  exist yet (#115), so this would keep dead code alive against an unspecified
  consumer. When #115 is built it introduces the trigger it needs, on the financial
  side of this line, with this record as the license.
- **Do nothing until the size path covers the trimodal case.** Rejected as a
  sequencing error: it makes retiring a wrong signal wait on an unrelated recall
  improvement. The two are now separate items.

The carve-out is explicit: `TITLE N`, `SEC. N`, and enumerators are not
appropriations vocabulary and stay load-bearing. So does GPO format plumbing
(chrome/watermark/hyphen handling), which names nothing.

This record governs the **bill-structure** path. The committee-report parser reads
`INCLUDING`/`LIMITATION`/`RESCISSION` to parse the comparative-statement table that
is our independent ground truth ([0009](0009-validation-ground-truth.md)). That is
financial-table parsing of a different document, not bill structure, and is out of
scope here.

## Consequences

Account-level breadcrumbs now exist only where the glyph-size path succeeds. On a
size-fail bill the deepest breadcrumb runs TITLE/SEC./subsection, with no account,
agency or major segment. On the current corpus that removes exactly one distinct
piece of structure, the incorrect 119-hr-1 fragment described above, and nothing
correct. It is still a real reduction in principle, and it will show as thinner
navigation on any future bill whose typography we cannot read.

That makes size-band coverage the single thing standing between a bill and its
account structure, with no second path behind it. Widening it is now a recall
priority rather than a nicety — the trimodal case that sends reconciliation bills
down the degraded path is tracked separately
([#508](https://github.com/AgoraDMV/DeltaTrack/issues/508)), as is the
line-number-independent pass for unnumbered layouts (#261).

The rule is enforced by a test that fails if appropriations vocabulary reappears in
a structural module. Two properties are what make it worth anything, because an
absence assertion that is merely present proves nothing:

- **Coverage fails closed.** It scans everything under `src/deltatrack` except a
  short allowlist naming the modules permitted to read this vocabulary (the
  financial layer, and the committee-report table parser under the 0009 carve-out).
  A new structural helper is guarded the moment it is added, rather than needing to
  be remembered. The allowlist is itself checked to name only modules that exist, so
  a rename cannot silently widen it.
- **Matching survives ordinary regex spelling.** Literals are normalised to letters
  before comparison, so `\s+`, bracketed spaces and escapes cannot slip a phrase
  past. A plain substring check would have missed all of them.

It is proved capable of firing rather than assumed to be: tests assert it flags the
retired pattern verbatim, flags escaped and whitespace-class variants of it, and
flags one planted in a newly-created module. Without those, a detector that had
quietly stopped matching would read as permanent compliance.

Reviewers get a clear question for anything touching heading or account detection:
*is this reading format, or is it reading appropriations English?* The second needs
this record amended, not a code review waiver.
