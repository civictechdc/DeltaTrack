# Source-signal inventory (PDF + XML)

What signals the source bill PDFs and XMLs carry, which we already use, which are
worth adopting, and which are confirmed absent or too weak — so we stop
re-investigating the same "could we use X?" questions. **Every measured number below is
produced by the committed reproducer** (the corpus-shape counts in this paragraph are
the one exception — they describe the input) and is a point-in-time measurement of the
current fetched corpus (102 XML versions across 31 bills, 17 with multiple versions;
87 bill PDFs across 18 bills), not a maintained invariant:

```
.venv/bin/python scripts/audit_source_signals.py
```

Snapshot: 2026-07-10. The script needs the full fetched corpus under `bills/` (see
`scripts/fetch_test_assets.py`), which a clean clone does not carry. Re-run it to
refresh the numbers; this is an audit record, not a spec — decisions it feeds become
issues / ADRs. Constraint context: the engine is deterministic and offline (no LLM, no
network, no auto-fetching XML to enrich a PDF diff), so every signal below is an
in-file signal read from the input the user already supplied.

## What we already extract

- **PDF** (`src/deltatrack/parsers/pdf_text.py`, `src/deltatrack/parsers/pdf_anchors.py`): codepoints; per-glyph
  char boxes → baseline clustering (visual lines), x-gaps (word spacing), line extent
  (justified column width, line-fullness split); text-matrix scale → glyph **size**
  (body/heading/major size bands), and per line the **letter size range** and the
  **case pattern** (whether each word's first letter prints larger than the rest: title
  case in small caps, the agency style, vs even small caps, the account style;
  `LineGeom.initial_caps`, [ADR 0022](decisions/0022-pdf-heading-convergence.md)). That
  is the complete set of PDFium signals we read.
- **XML** (`src/deltatrack/bill_tree.py`, `src/deltatrack/structure_tree.py`, `src/deltatrack/diff_bill.py`): the structural tag
  hierarchy + text, the `form` metadata block, and three attributes — `@id` (stored on
  `BillNode.element_id`, **not** used for matching), `@display-inline`,
  `@display-enacting-clause`.

## Validated findings, ranked by value

| Signal | Source | Verdict | Value | Where it helps |
|---|---|---|---|---|
| **Font name = chrome discriminator** | PDF | Adopt (supplement) | Medium | Margin numbers are a different font from body on 99.9% of numbered lines; chrome is Helvetica/Symbol. "Any Helvetica glyph on a body page ⇒ chrome" is orthogonal to today's regex/bullet heuristics — a general robustness backstop (the #140 running-footer case is already fixed; also aids the #141 enrolled-bill fallback). |
| **`@changed` / `@reported-display-style`** | XML | Adopt (corroboration) | Medium | The bill's own change markup. Corroborates our computed diff, densest on engrossed-amendment versions (21/21). |
| **`toc-entry@level` / `header-in-text@level`** | XML | Adopt where present | Medium-low | GPO's explicit level labels — an independent oracle to validate/repair inferred hierarchy. Partial: `toc-entry` gives coarse levels, `header-in-text` finer + appropriations tiers; 29–38 of 102 files. |
| **`@id` as a matching anchor** | XML | Marginal — low priority | Low | ~93 net-new matches corpus-wide over the current matcher; reliable only where regeneration hasn't happened. See below. |
| **Bold as a heading tier** | PDF | Investigate — do **not** adopt yet | Unproven | Class-dependent and unreliable in measurement; not the clean second tier an early spot-check suggested. See below. |

### Font name = chrome/margin discriminator (PDF) — the solid PDF win

Role separation is clean: margin line-numbers are a different font from the body on
**8965/8971 numbered lines (99.9%)**, and page chrome (VerDate, running header/footer,
watermark, bullets) is Helvetica/Symbol. The literal names are **print-class-dependent**
— bill bodies are `DeVinne`, but enrolled / engrossed-amendment-senate / committee-print
bodies are `NewCenturySchlbk` — so any rule must key on **role** (margin / body / chrome),
never a hardcoded name. The motivating case was **#140** (running-footer corruption, since
fixed): on those bills the surviving footer, VerDate line, and scrambled watermark fragments
are all Helvetica while body prose is not, so a "Helvetica ⇒ chrome" drop catches the
bulleted header, the unbulleted footer, and the watermark uniformly — one role rule where
today's code chases each pattern with its own regex. Guard: a small fraction
of glyphs return an empty font name (GetFontInfo miss), so font must supplement, not
replace, the position/regex gates. (The precise Helvetica-in-body rate and empty-font-name
rate come from a one-off FFI sweep, not this script; the committed script measures the
99.9% margin/body separation and the chrome/head-role splits.)

### The bill's own change markup (XML)

Reported and (especially) engrossed-amendment versions carry GPO's own change markup.
`@changed` appears in 40/102 files; `@reported-display-style` (italic/strikethrough) in
33/102. Presence of `@changed | @reported-display-style` by version type:

| version bucket | with markup / total | | version bucket | with markup / total |
|---|---|---|---|---|
| engrossed-amendment | 21/21 | | reported | 8/23 |
| reported-to-senate | 3/3 | | engrossed-in | 3/17 |
| enrolled | 8/12 | | referred / received / placed-on-calendar | 1/7, 1/5, 1/4 |
| introduced | 0/10 | | | |

*The `reported-to-senate` bucket is the pre-#10 slug this 2026-07-10 snapshot was measured against; post-#10 the same versions enumerate as `reported-in-senate` (govinfo canonical `rs`). The 3/3 count is unaffected — the label is not re-run against the renamed corpus.*

Caveats that shape any use:

- It is **version-internal** — what this version changed relative to its *own*
  predecessor base, not a `v_a → v_b` annotation. So it corroborates/validates our
  computed diff (strongest on amendment versions), it does not replace the diff engine.
- **Introduced is clean (0/10); enrolled is not (8/12).** Enrolled keeps residual inline
  markers carried forward from the amendment stage even though the structural `@changed`
  flag mostly drops.
- `<added-phrase>`/`<deleted-phrase>` are mostly **empty paired span-delimiters** (79/118
  and 11/11 empty) — but a third of `<added-phrase>` *do* carry text, so a consumer must
  read both: attributes on the block/`@changed` elements (where the bulk lives), and the
  first marker's *tail* text between an empty pair. There is no `<changed>` element.

### `@id` as a matching anchor (XML) — validated down from "top pick"

An initial sample suggested `@id` was byte-stable across same-chamber versions and a
cheap, high-value key for structural matching (#170). The corpus refutes the general
claim. Structural-element `@id` overlap across every consecutive version pair, split by
the *kind* of transition (mean per-pair shared/new; raw XML and the consumed
`element_id` agree in shape):

| transition | raw | element_id (consumed) | shape |
|---|---|---|---|
| cross-chamber **hand-off** (a chamber receives the other's passed text) | 1.00 | 1.00 | stable, all 16 pairs |
| cross-chamber **amendment** ping-pong | 0.00 | 0.00 | regenerated |
| same-house (introduced→reported→engrossed) | 0.33 | 0.48 | **bimodal** — some bills preserve, most regenerate |
| same-senate | 0.33 | 0.29 | bimodal (mostly zero, a few full) |
| enrollment | 0.00 | 0.00 | regenerated |

Measured against the current matcher, id-equality is low-yield. Of ~30.6k matched pairs
carrying ids on both sides, id-equality flags **93 candidate pairs the path matcher
missed** (84 header-identical) — but only **59 are conflict-free**; the other **34
contradict an existing matcher pairing** (30 old-side, 25 new-side), i.e. id-equality and
the path matcher disagree there and one of them is wrong. The clean lift is *not* on the
easy hand-off (0 net-new — verbatim text is trivially matched) and *not* on cross-amendment
(35 candidates but only 4 clean; the rest are conflicts). It concentrates in **same-senate
(50 of the 59 clean)**, with 5 more in same-house — i.e. the id-preserving same-chamber
bills. So `@id` is a thin supplement: ~59 otherwise-missed matches corpus-wide, and
`normalize_bill` already drops *all* `element_id`s on one file
(`115-hr-244/4_engrossed-amendment-senate`), so even wiring the value in is not free.
Verdict: low priority for #170. If used, gate it to same-chamber-consecutive pairs (which
keeps the 55 clean same-chamber additions and discards the mostly-conflicting
cross-amendment candidates) and treat a matcher conflict as a flag to resolve, not an
override.

### Bold as a heading tier (PDF) — investigated, not adopted

An early spot-check suggested `SEC./DIVISION` run-in heads are a bold face and could be a
second heading tier the size-only detector misses. Corpus measurement does **not** support
adopting it. Fraction of head-prefixed lines whose leading label token is bold, by head
kind × body print-class:

| head kind | DeVinne-class | NewCenturySchlbk-class |
|---|---|---|
| `SEC.` / `SECTION` | 0/42 (0%) | 82/184 (45%) |
| `TITLE` / `DIVISION` | 0/62 (0%) | 15/111 (14%) |
| account/agency (`DEPARTMENT OF`…) | 0/10 (0%) | 0/11 (0%) |

Bold is absent as a head signal in DeVinne-class bills (the common bill print), and only
~45% present on `SEC.`-prefixed lines in NewCenturySchlbk-class prints — and that
denominator still includes `SEC.`-prefixed TOC and cross-reference lines, so the true
head rate is unresolved. The one clean result is a negative: account/agency heads are
never bold (0/21), so bold would not false-positive there. Net: bold is not a reliable
general heading signal on the current evidence; confirming it would need per-line
ground-truth head labels, not a prefix heuristic. Do not build on it yet.

## Confirmed dead ends

Recorded so we do not re-investigate them. All measured on the corpus.

- **No PDF document outline/bookmarks** (0/87) and **no tagged structure tree** (0/87).
  GPO ships untagged PDFs; the size/position heuristics in `pdf_anchors.py` are
  necessary, not a stopgap. There is no logical-structure shortcut to steal.
- **No PDF color or render-mode signal.** Every sampled in-body text object is fill mode
  and black/white; the only non-black is the white watermark we already strip.
- **No PDF vector grid in *bill* text.** Path objects on sampled body pages: 25 across all
  plain bill versions vs 2580 in the reported/committee-print class. Ruled table borders
  exist only in committee-print PDFs (reported bills + CRPT reports) — relevant to the
  financial-semantics epic #147 if we parse those money tables, not the bill-text diff.
- **No structured dollar amounts in XML.** Zero `<amount>`, `@currency`, `<quantity>`
  across 102 files; every figure is inline text inside `<text>`. Money extraction must
  parse strings. (Two token look-alikes are cleared: `appropriations-*` are structural
  headings; `colspec@min-data-value` (142 occ) is table-layout metadata.)
- **Italic** exists in bill bodies (68/87 files) but is a whole-document mode in Senate
  amendments and only scattered emphasis elsewhere, so it is not a reliable structural
  signal — noted, not adopted.

## If we adopt any of these

Each adopted signal earns a standing gate at that point (a corpus assertion in the test
suite), not a one-off probe — this audit script stays the reproducer, the gate locks the
behaviour. The actionable candidates: font-name chrome → a new robustness issue (the #140
footer case is fixed; benefits the #141 enrolled-bill fallback); change markup → a new
corroboration feature; `@id` anchor → recorded on #170 as a guarded, low-priority
supplement. Bold-as-heading and the committee-print table grid are parked pending a
ground-truth head study and the financial-semantics epic #147 respectively.
