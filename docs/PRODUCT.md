# Product Direction

> This document is the source for the product's vision, users, goals, scope, principles, strategy, and roadmap.
>
> GitHub Issues and Projects track execution. Architecture Decision Records (ADRs) document significant technical decisions. Research documents record the evidence used to inform those decisions.

## Product summary

DeltaTrack is an open source tool built to allow *anyone* to compare two versions of U.S. draft legislation. DeltaTrack is deterministic, auditable, reproducible, and supports PDF and XML versions of legislative text. We support both humans and agents as first class users.

Three things make it distinctive: it is publicly available rather than internal to Congress, it works from the PDFs that circulate before an XML version exists, and its output is as legible to an agent as it is to a person.

## Problem

This project originated from a challenge raised by a Congressional staffer. They sought to do the following within 3 days of receiving a new proposed version of an appropriations bill: 
1. Compare 2 versions of a large appropriations bill (100+ pages), the proposed draft and the prior accepted version
2. Identify the dollar amount differences in the draft version vs. the prior version
3. Review and prioritize which changes require a response from their office
4. Submit a response as proposed changes for the next version

The staffer already had enough context and knowledge to execute steps 3 and 4. The challenge is that reading and diffing the bill versions by hand would consume all of the available time before they could draft and submit proposed changes. Without an accurate and timely way to diff the bill versions, they could not guarantee responses to critical changes that affected their district. 

Similarly, discussions with journalists and lobbyists yielded similar problems. They know what issues are important to them and what they want to respond to, they don't have the ability to compare bill versions quickly and accurately enough to provide timely responses, such as articles or letters to Congress to influence the legislative process. 

> Timeliness of responses is a key problem for DeltaTrack users and a primary driver of the solution architecture and design choices. Often, bill versions arrive first as PDFs from committee websites. There can be lag time of days (or longer) before an XML version is available on Congress.gov and additional lag between when that XML version is available through the GPO. XMLs are more accurate as a source of diffs, but waiting for them means that users may miss the response window to influence and respond to legislative changes. A solution that does not support timely responses is unacceptable. We will consider and make compromises where needed to allow for timely responses from our users. Where and when we make these compromises, we will be explicit. 

## Vision

DeltaTrack is the default tool to view and compare two different versions of draft legislation during the legislative process.

DeltaTrack is the default tool for autonomous agents supporting research related to U.S. legislative measures. Specifically, agents asked to "Compare 2 versions of a bill" should default to using DeltaTrack. 

## Users

### Primary users
DeltaTrack's users work with legislation under a deadline. Congressional staffers set the product's hardest constraints, and agents are a first-class access surface held to parity with the human one. Where the two conflict, the staffer's need wins.

**Congressional staffers**. Staffers serve Members of Congress and Senators in their offices. Their primary use case is comparing a new draft of legislation vs. its prior draft. They receive drafts as PDFs without immediate XML access and are the primary reason that DeltaTrack must support PDF vs. PDF version comparisons. Staffers use provisioned and hardened computers with access restrictions, cannot install external software without approval, and cannot leak draft legislation. They do not have the ability to install local LLMs and we do not have visibility or access to their internal AI tools. We inform our security and privacy requirements according to practices required from the [House Chief Administrative Officer](https://cao.house.gov/) and the [Senate Sergeant at Arms](https://www.senate.gov/about/officers-staff/sergeant-at-arms.htm), the IT authorities for their respective chambers of Congress. Staffers care about diff accuracy (can I trust the output?) and speed (can I generate the output fast enough to take action?). 

> Staffers have access to internal tools that compare draft legislation versions. These tools are the [Text Analysis Program (TAP) and the Comparative Print Suite](https://congressionaldata.org/congressional-data-task-force-recap-june-11-2026/). However, access is not broadly available to all staffers, and neither tool is available for public use at this time. 

**Agents**. We expect that the majority of users will either interact with DeltaTrack through an agent (reporters, researchers, lobbyists) or use DeltaTrack to generate an output for an agent, such as ChatGPT, Microsoft Copilot (staffers), or Claude. We include agents as a primary user because a product that is difficult, opaque, untrustworthy, or undiscoverable to agents will not reach our primary users. 

### Secondary users

**Reporters and Lobbyists**. Similar requirements to staffers without the more rigorous technology restrictions. Reporters need to compare draft versions of legislation quickly to inform their writing and meet deadlines. Like staffers, they also receive PDF versions of draft bills from committees before receiving XML versions through public committee announcements. Similar to staffers, reporters and lobbyists care about diff accuracy and speed. Unlike staffers, reporters and lobbyists could interact with DeltaTrack exclusively through agents, such as ChatGPT or Claude. 

**General public**. Members of the general public may have passing interest in a specific bill. Unlike other user groups, we don't expect them to interact with DeltaTrack directly at all. Instead, they are more likely to ask an agent, such as ChatGPT or Claude, about a specific bill. In this case, the agent would use the DeltaTrack library to answer the user's questions. 

**Researchers**. Researchers may have an interest in following the evolution of a particular legislative measure over time and research on a particular bill can use DeltaTrack. However, DeltaTrack's focus on comparing two different draft versions of legislation does not support comparing the provenance of legislation from multiple prior bills. We may revisit this at a future point, but **for now we are focused on supporting users who care about comparing 2 different versions of a bill to influence the legislative process under a deadline.** 

### User needs

The core job to be done for DeltaTrack is to compare two versions of draft legislation from two source PDF or XML files and deliver the outputs of that comparison to the user. The outputs should be for the human and their agent in formats that are easy for a human to read and skim and easy for an agent to review and summarize. This process is similar for staffers, reporters, and lobbyists. 

Workflow: Evaluate and respond to a new bill version
1. Receive a new version of legislation. Either via email (staffer) or the GPO/committee announcement (all other user groups)
2. Find the most recent prior version of that legislation. Either via email (staffer) or the GPO/congress.gov (all other user groups)
3. Compare the new version vs. the old version of the legislation. Specifically: 
   - Which items were modified? 
   - Which items were moved? 
   - Which items were added? 
   - Which items were removed? 
4. For items that changed, determine the importance and meaning of those changes. From a staffer's perspective:
   - Do these changes affect a policy area our constituents care about? 
   - Do these changes affect our district? 
   - Do these changes affect items that were drafted by my Member of Congress/Senator?
5. Draft and send a response to the changes. For staffers, this could be a proposal for new changes. For reporters, this could be an article. For lobbyists, this could be an email or a document to influence the legislative process.  

Simplified, the workflow is: 

> Gather Bill Versions -> Find the Differences -> Assign Meaning to the Differences -> Take Action. 

DeltaTrack's focus is **Finding Differences.** We rely on users to choose the two versions to compare, and to understand and assign meaning to the differences we find. 

## Goals (in order of Priority)

1. DeltaTrack is the preferred publicly available tool for comparing 2 bill versions.
2. DeltaTrack is the preferred tool for comparing PDF versions of bills before XML versions are accessible. 
3. DeltaTrack is the most accurate deterministic and reproducible publicly available tool for comparing 2 bill versions. 
4. DeltaTrack is the most used tool by agents for the prompts: "What changed in this updated legislation draft?" and "What are the differences between these 2 bill versions?"

## Scope

### In scope

* Compare two different versions of legislation (XML or PDF format) for changes (a diff)
* Report what was added, removed, modified, and moved between the two versions
* Support any bill type (HR, S, HJRES, and so on), not only appropriations
* Publish the diff as a documented, versioned JSON contract, so agents and other tools can consume it directly ([ADR 0006](decisions/0006-canonical-diff-contract.md))
* Render a self-contained HTML report a person can read with no additional software
* Compare financial changes between 2 versions of appropriations bills

Bill acquisition (`tools/`) ships in this repository as supporting tooling, not as part of the product surface ([ADR 0016](decisions/0016-product-tooling-surface-split.md)). It exists so a user can obtain public versions to compare; the product is the comparison.

### Out of scope

* Compare more than 2 versions of a piece of legislation. 
* Assign meaning to a diff. DeltaTrack does not interpret, judge, or rank changes; the user does. Per the workflow above, we own Finding Differences and nothing downstream of it.
* Take actions based on the result of diffs. For example, DeltaTrack is not a tool to draft comments or responses to legislative changes. 
* Track a bill across its lifecycle, or trace provenance across multiple prior bills (see Researchers above; we may revisit provenance later).

## Product principles

Architectural decisions are tracked in [Architectural Decision Records](decisions/). These guiding principles inform the overall product design in order of priority. 

### 1. Accuracy and trust

Accuracy and trust in the bill comparison methodology and outputs are the most important contract between DeltaTrack and users. If the diff is not accurate or trustworthy, the product is unusable.

### 2. Deterministic Differ

Tied to our focus on accuracy and trust, the diffing methodology must be deterministic, reproducible, and auditable. **Every user comparing the same 2 bill versions with the same DeltaTrack version must get the same diff result, no exceptions.** We reject methodologies that are not deterministic, reproducible, or auditable as solutions to generate diffs. While these methodologies may have merit, they do not meet the needs of this project. 

### 3. Disclosure Over Silence

Our achievable accuracy is bounded by the source. A PDF carries less structure than the XML of the same bill, so a PDF comparison can be less precise than an XML one. We accept this limitation, and we do not drop PDF support because of it. A comparison the user can run today beats a more precise one that arrives after the response window closes. What we do not accept is an undisclosed limitation. We maximize accuracy within what the source allows, and where a result is uncertain, incomplete, or degraded by the source, the output says so. Uncertainty that only we can see is a defect.

### 4. Confidential Data Cannot Leave the User's Machine

Draft legislation may be public or private information, depending on when and how it is released. We seek to guarantee the privacy and security of our users by ensuring no uploaded documents or outputs (bill versions) leave the user's machine. We treat user provided information as confidential and private. 

*Note.* The live site at [deltatrack.agoradmv.org](https://deltatrack.agoradmv.org) is currently in violation of this rule: its active comparison path uploads files and diffs them on the project's server. The page discloses this and warns against uploading non-public bill text, and the local CLI is unaffected. This is a known, deliberate interim state while the in-browser path is finished, tracked in [#112](https://github.com/civictechdc/DeltaTrack/issues/112) and recorded in [ADR 0011](decisions/0011-local-only-processing.md). 

### 5. Leave Nothing Behind

DeltaTrack writes bill content and diff output only where the user directs it. It keeps no cache, index, or temporary copy of bill text the user did not ask for. Program state (which version is installed) and user preferences (default views, saved filters) are permitted and must contain no bill content. This binds the product; the acquisition tooling in `tools/` writes public bill versions to disk by design (see Scope). See [ADR 0005](decisions/0005-contained-two-version-tool.md), whose scope test asks whether a feature stores bill data rather than whether it stores anything at all. 

### 6. AI Is A User, Not A Product

We assume that agents will use DeltaTrack as much, if not more, than human users. Therefore, we seek to be discoverable, legible, and easy to use for agents and allow the same functionality between agents and humans. 

### 7. Complement Existing Tools, Don't Replace Them

DeltaTrack has a narrow, defined scope that may be of use to other tools. We seek to support and encourage use cases that leverage bill diffs. We will do that by being the best tool for comparing legislative versions. 

## Execution and supporting documentation
* **Backlog and active work:** [GitHub Project](https://github.com/orgs/civictechdc/projects/24)
* **Issues:** [GitHub Issues](https://github.com/civictechdc/DeltaTrack/issues)
* **Architecture decisions:** [`docs/decisions/`](decisions/)
* **Research:** [`docs/research/`](research/)

When product direction changes, update this document. When implementation plans change without changing product direction, update the relevant GitHub issues or project instead.
