# 11. Process user-provided bill content only on the user's machine; no channel may send it off-device

- Status: Accepted
- Date: 2026-06-27

## Context

DeltaTrack is built to compare bills a staffer already has in hand, including versions
that are **not yet public** — pre-introduction committee prints, chair's marks, and
genuine discussion drafts ([0010](0010-pdf-pipeline-pre-publication.md)). That content
is sensitive, pre-decisional material. A leaked draft appropriations bill — revealing
what an office is proposing before it chooses to say so — is a real harm to the
staffer and the office, not a hypothetical.

[0005](0005-contained-two-version-tool.md) puts both halves of this in DeltaTrack's
scope test: a feature is out of scope if it requires bill data to leave the user's
local environment, or to be stored anywhere. That settles the principle. It does not
settle what the principle costs, and the two halves are worth keeping distinct because
a channel can honor one and breach the other. A server that received an uploaded
draft, diffed it, returned the result, and stored nothing writes no file and keeps no
state, yet the draft still left the user's machine. Persistence and exfiltration are
different risks, and the sensitive-draft case turns on the second one.

This question is live because the delivery channel is unsettled. Candidates include a
static HTML file, a local native app, a packaged executable, a browser extension, and
a server-rendered web app (the FastAPI path). They differ precisely on this axis: a
server-rendered web app that ingests an uploaded bill sends that content to a server we
operate; the others can run the comparison entirely on the user's machine.

Two further constraints from the staffer environment bear on the channel choice, though
not on the privacy rule itself. Congressional offices gate software through different
bodies in each chamber — the **Senate Sergeant at Arms (SAA)**, whose Computer Center
procures and installs from a Rules-Committee-approved list, and the **House Chief
Administrative Officer (CAO)**, whose House Information Resources approves software
through a request process. The detailed rules live on internal networks and are not
public. The practical effect: a channel that runs in the already-approved browser or
needs no install at all clears the gate easily, while an arbitrary executable does not —
hardest in the Senate, where install is centralized.

## Decision

**Bill content the user provides — an uploaded or pasted PDF, XML, the extracted text,
or the diff derived from it — must never leave the user's machine.** All processing of
that content happens locally. We will not ship a delivery channel that transmits
user-provided bill content to a server we operate or to any third party, and this holds
**regardless of whether that server would store anything.** The risk we are closing is
the content leaving the device, not the content being kept.

We do **not** branch this rule on whether a given bill is public. A path that sent
content off-device "only for already-public bills" would depend on correctly
classifying every input first, and a single misclassified draft is an unacceptable
leak. Treating all user-provided content as if it must stay local removes that failure
mode entirely. The cost is small: the most sensitive inputs (drafts) require local
processing anyway, so a public-bill server path would buy little and add a sharp risk.

This rule governs **user-provided content**, not all network access. Fetching an
already-published bill from an official source (govinfo, [0004](0004-govinfo-bulk-data.md))
is retrieval of public data, not exfiltration of the user's material, and is not what
this record forbids — though such fetching remains subject to 0005's offline-first
posture and is a channel-design question, not a privacy one.

The **specific delivery channel remains an open decision.** What this record settles is
the constraint every candidate must meet: it must process user-provided content
locally. That rules out a server-rendered web app *as the path for uploaded bills*; it
permits a static HTML file, a local app, a browser extension, or a browser app doing
client-side extraction (shown viable for PDFs in [0003](0003-pdfjs-client-side-viability.md)).

Alternatives:

- **Allow a server-side path for bills classified as public.** Rejected. It reintroduces
  the exact risk this record exists to remove: a draft misread as public would be
  transmitted, and the consequence of that one error is severe. The marginal benefit
  (offloading work for public bills) does not justify a classification step that can
  fail open.
- **Lean on 0005 and write nothing.** Rejected. 0005 states the rule but not what it
  rules out. Which delivery channels the rule forecloses, why a published-bill exception
  fails open, and what it means for telemetry all follow from the rule rather than
  restating it, and without them the same ground gets re-argued the next time a hosted
  channel is proposed.

## Consequences

- The delivery channel choice is **constrained but not made**. Any candidate that
  processes uploaded bills server-side is out for that purpose; browser-client,
  static-HTML, and local-app channels remain in. The pick among the survivors — driven
  also by the SAA/CAO install-gate reality above — is a separate open question to be
  decided on its own, tracked in
  [DeltaTrack#112](https://github.com/civictechdc/DeltaTrack/issues/112).
- The server-rendered web channel is not invalidated as a way to serve the UI, or to
  work with already-public bills where no user-provided content is transmitted. But a
  hosted path that ingests uploaded bills **is noncompliant with this rule**, whoever
  operates it. The sensitive-input path has to run client-side.
- This is why the client-side PDF.js extraction result ([0003](0003-pdfjs-client-side-viability.md))
  matters beyond convenience: it is what makes a browser channel able to honor this rule
  for the hardest input.
- The rule is conservative on purpose and costs little, because the engine is already
  local and stateless ([0005](0005-contained-two-version-tool.md)); this record works
  out what that commitment requires of the delivery channel.
- Telemetry, crash reporting, or "send us the file that failed" diagnostics that would
  carry bill content off-device are foreclosed by this rule. Diagnostics must be local
  or content-free.
