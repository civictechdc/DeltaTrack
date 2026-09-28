# 12. Recover PDF heading levels from deterministic geometry; accept the prose-leading agency gap

- Status: Accepted
- Date: 2026-06-28

## Context

The leveled heading tree (DeltaTrack #54) reconstructs a bill's
`TITLE > major > agency > account` hierarchy on both pipelines. The XML pipeline
carries the levels as tags. The PDF pipeline has to *infer* them, and the engine
must do so deterministically and offline ([0008](0008-deterministic-engine.md)) —
no LLM, no network, and in particular no auto-fetching the XML to enrich a PDF diff
(that is input automation plus a network call the engine forbids,
[0005](0005-contained-two-version-tool.md) / [0010](0010-pdf-pipeline-pre-publication.md)).

GPO prints carry less level information than the XML, so the question is not "how do
we recover every level" but "which levels are recoverable from the print at all, and
by what signal." Slices A–C of #54 recovered grouping headers (#103), carry-over
agencies (#104), and the major/department level (#105) from glyph size, casing, and
position. Two boundaries resisted those signals because the two sides are
**typographically identical** — same size, same casing, same horizontal centering:

1. **Prose-leading agency vs account.** An agency heading followed directly by
   appropriation prose looks exactly like an account heading followed by prose. This
   is the majority of the agency level in Defense-class bills (H.R. 8774: 59 of 67).
2. **Stacked major vs wrapped major.** A title can print two *distinct* stacked
   department headings (`CORPS OF ENGINEERS—CIVIL` / `DEPARTMENT OF THE ARMY`), which
   look the same as one *long* department name wrapped across two lines
   (`DEPARTMENT OF HEALTH AND HUMAN` / `SERVICES`). The greedy join in #105 mashed the
   stacked case into one major on 4 of the 12 subcommittees.

Spike #106 measured every CAPS heading line across one reported-in-House print from
each of the 12 FY2025 appropriations subcommittees, reusing the per-glyph box walk
already in `pdf_text` (geometry was extracted there to derive glyph size, then
discarded — so the probe added zero PDFium calls). The findings:

- **x-centering is invariant across levels.** Heading-band headings center at x≈318,
  majors at x≈320; the 2 pt offset is a constant per-band artifact, not a level
  signal (agency and account both 318; stacked and wrapped major both 320).
- **Vertical leading is uniform** at ~26 pt (σ < 1) above every line in all 12 bills.
  There is no extra space above a new level to detect.
- **Horizontal line-fullness separates boundary 2.** A heading line wraps only
  because the next word did not fit, so a run line that broke *early* — its
  successor's first word would have fit within the justified text column — is an
  intentional break between two stacked headings. Across all 12 bills against the
  reviewed `major_vocab.json` golden: 8/8 clean bills had zero false splits and 4/4
  stacked pairs split at every printed-line boundary. The class margin is ~40 pt
  (split line sums 250–315 pt, wrap sums 354–418 pt), robust to the small constants
  and to the one bill whose column measured 311 pt rather than 339.
- **No geometric signal separates boundary 1.** A prose-leading agency is a single
  heading-band line followed by prose: there is no second heading line to test for
  fullness, and x-centering and leading are null as above. The spike did not measure
  the letters themselves: an agency prints in title case in small caps, an account in
  even small caps ([0022](0022-pdf-heading-convergence.md)).

## Decision

We will recover the PDF heading levels from **deterministic geometry and structural
position only**, and accept the one boundary that no such signal separates.

- **Boundary 1 (prose-leading agency vs account): accept the gap.** A lone
  heading followed by prose is emitted as an account. We do not guess it is an
  agency. Its case pattern ([0022](0022-pdf-heading-convergence.md)) is used to end
  the reach of an earlier agency, never to re-label the heading itself, which has not
  been measured.
- **Boundary 2 (stacked vs wrapped major): split on line-fullness.** The major
  detector splits a post-`TITLE` body-size run at each line-fullness hard break:
  `w_i + space + first_word_width(line_{i+1}) ≤ column_width − slack` means line *i*
  broke early and the two lines are separate majors; otherwise they are one wrapped
  name. `column_width` is `median(content_right − content_left)` over the document's
  justified body prose. Implemented in `parsers/pdf_anchors.py` (`_major_anchors_by_size`)
  on the per-line extent now exposed by `parsers/pdf_text.py` (`LineGeom`), still
  within the same char walk and still zero new PDFium calls (#130).

This keeps level recovery inside the determinism contract: every level the PDF
pipeline emits traces to a fixed geometric or positional rule, the same on every
run ([0008](0008-deterministic-engine.md)).

Alternatives considered:

- **A lexical rule for the agency level** (`DEPARTMENT OF` / `OFFICE OF`
  line-starts). Rejected. It misses real agencies that do not start with those words
  (`HOUSE OF REPRESENTATIVES`) and pulls appropriations content words into structure
  detection, which DeltaTrack #114 deliberately keeps out. A geometric rule that fails cleanly
  is preferable to a lexical rule that fails silently on unseen vocabulary.
- **An XML cross-reference to label the PDF levels.** Rejected as automatic
  enrichment: auto-fetching the XML is a network call and input automation the engine
  forbids ([0005](0005-contained-two-version-tool.md) / [0008](0008-deterministic-engine.md)).
  It counts only when the user supplies the XML, which is just the XML pipeline, or as
  a concern for something built on top of DeltaTrack.
- **A model to classify the ambiguous headings.** Rejected by
  [0008](0008-deterministic-engine.md): a model may read a finished diff, never
  compute one.

## Consequences

- The PDF heading tree reaches `TITLE > major > agency > account` with the major
  level fully separated (the 4/12 stacked residue from #105 is retired) and the
  carry-over agency level recovered where it is followed by another heading (#104).
- Defense-class bills keep a known, documented agency gap: prose-leading agencies
  surface as accounts. This is recorded as an accepted limitation, not a bug to chase
  with a heuristic. Where the agency level matters for those bills, it comes from the
  XML pipeline (which the user already supplies for a published bill,
  [0010](0010-pdf-pipeline-pre-publication.md)).
- The line-fullness rule carries one residual edge, absent from the FY2025 corpus:
  two stacked departments whose *upper* line nearly fills the column would read as a
  wrap (no early break to detect). A within-a-single-line compound
  (`DEPARTMENT OF STATE AND RELATED AGENCY`) cannot be split by any line-granular
  geometry. Both are documented at the detector and bounded by the corpus evidence.
- The reproducibility/audit properties hold for the new levels: the validation corpus
  (one print per subcommittee, pinned by `major_vocab.json`) can assert exact level
  sets because the rules are deterministic, the same argument as
  [0009](0009-validation-ground-truth.md).
- The canonical contract is unaffected: deepening the `path` array is not a schema
  change ([0006](0006-canonical-diff-contract.md)), so neither converter changed.

<!--
References: DeltaTrack #54 (epic), #105 (major level), #106 (signal spike),
#130 (line-fullness split). Builds on 0008, 0005, 0009, 0010; constrained by #114.
-->
