# Frequently Asked Questions

The questions that come up most often in meetings and discussions about DeltaTrack. Fuller reasoning lives in the [Architecture Decision Records](decisions/); [PRODUCT.md](PRODUCT.md) covers users, scope, and principles.

## Why not use git or an off-the-shelf diff tool?

This was the first thing we tried, and it is the most common question from new contributors. It fails on both source formats, for different reasons.

**XML.** Every bill version is authored in XML, and a generic XML differ will report which nodes changed. What it will not tell you is what changed *in the bill*. Versions often contain substantial rewrites, and once a paragraph is edited, renumbered, moved, or folded into a larger consolidated bill, the node-level differences pile up until the output is unreadable. 

**PDF.** GPO renders bill PDFs under a detailed style guide that numbers every line and hyphenates words across line breaks. Change one word early in a section and every line after it reflows, taking the line numbers and hyphenation with it. A PDF or text differ shows giant blocks of changed text, almost all of it typesetting rather than substance.

DeltaTrack parses each version into the bill's own structure and diffs that, so renumbering and reflow drop out of the result. 

## Why support PDF files when there is an XML version of every bill?

Because the PDF is usually the only version available in time to act on. A bill is authored in XML, but that XML is not public until GPO publishes it, which happens after the committee stages. At a chair's mark or during markup, the public artifact is a PDF posted by the committee, and the XML appears on govinfo and congress.gov roughly one to two days after the bill is formally introduced or reported. The Senate publishes no bill-text XML on its own site at any stage. Staffers most often receive a new draft as an emailed PDF.

That pre-publication window is exactly when the diff is most valuable to users. Refusing to support PDFs would mean telling them to wait for a version that may not arrive until after decisions are made.

The cost is accuracy. PDF extraction reconstructs structure the file does not state explicitly, so it is lossier than XML and carries the heaviest validation burden in the project. Once an authoritative XML version exists for the version in hand, that is the source to use. See [ADR 0010](decisions/0010-pdf-pipeline-pre-publication.md) and [bill-publishing.md](bill-publishing.md).

## Why not use an LLM to generate the diff?

Because the diff has to be a record a user can stand behind in an argument about what changed. That requires three properties: deterministic, auditable, and reproducible. In practice it means any two people running the same two files through the same version of DeltaTrack get exactly the same result, and every change the tool reports traces back to a fixed rule rather than a judgment call that might come out differently next time. A model's output is not guaranteed identical on a re-run and cannot be traced that way. 

An LLM may **read** a finished diff, but never create one. Downstream LLM use is supported and actively encouraged. Reports ship with ready-made questions to paste into an assistant, and the canonical JSON output is designed for agents to consume.

This is also not a rule about how the project is built. Contributors are welcome to use LLMs to write code. See [ADR 0008](decisions/0008-deterministic-engine.md).

## Why not have the user install a local LLM?

Three reasons, and the first is immediately disqualifying. A local model is still nondeterministic, so it fails exactly the same test any LLM does. Same inputs and same version have to produce the same output, and moving the model onto the user's own hardware does not make it deterministic.

The other two are about who uses DeltaTrack. Staffers work on provisioned, hardened machines where installing anything requires approval, so a bundled model is an install blocker for the primary user. Many of our users are not technical at all, and asking them to set up and run a model locally creates more barriers to using DeltaTrack.

## What is the value of DeltaTrack if Congress already has similar tools?

Congress does provide staffers with tools that do similar work, specifically the [Text Analysis Program (TAP) and the Comparative Print Suite](https://congressionaldata.org/congressional-data-task-force-recap-june-11-2026/). Our understanding is that access is not broadly available to all staffers at this point, and that neither tool is available publicly at all. Reporters, lobbyists, researchers, and the public have no equivalent, and neither do the agents working on their behalf.

DeltaTrack is publicly available, works on the files a user already has, including drafts that no public system holds yet, and produces output an agent can read as easily as a person can. The aim is to complement those tools, not replace them.
