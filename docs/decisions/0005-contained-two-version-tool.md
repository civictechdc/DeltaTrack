# 5. Keep DeltaTrack a contained two-version tool

- Status: Accepted
- Date: 2026-06-27

## Context

Questions arise over the scope of DeltaTrack and what features or potential additions
should be considered in scope vs. out of scope for DeltaTrack vs. another tool.

## Decision

We will keep DeltaTrack the ephemeral, local tool that compares **two** versions of a
bill. Its principles are simplicity, accuracy, speed, offline operation, and a safety
contract: uploaded bill data does not leave the user's local machine, or persist on it.
The user provides the two versions; the tool produces a self-contained report.

Other products may build on DeltaTrack, consuming its output through the canonical JSON
contract ([0006](0006-canonical-diff-contract.md)). Being usable that way is a by-product
of being a trustworthy engine.

The test for whether a feature belongs in DeltaTrack is:

1. Is the feature **deterministic and reproducible**? If not, it should be considered
   out of scope.
2. Does the feature **require bill data to leave the user's local environment, or to
   be stored anywhere**? For example, does it require an external service? If so, it
   is out of scope. We apply this rule to protect our users whose draft bill versions
   are considered private and confidential.
3. Does the feature require **comparing more than 2 versions of a bill**? If so, it
   should be considered out of scope.

Test 2 is about **bill data**, not about state in general. Program state and user
preferences hold no bill data and are in scope: which version of DeltaTrack is
installed, or that a user prefers a money-only view, is not what this rule protects. A
cache of extracted bill text is, and stays out of scope.

These are not permanent decisions and they may change over time. They also do not indicate
disapproval or hostility towards projects that do the above. We want to support those use
cases, but we will not do it inside of DeltaTrack.

## Consequences

- Every "does this belong in DeltaTrack?" question now has a default answer: if it is not
  deterministic, exposes or stores the user's bill version data, or compares more than 2
  versions of a bill, it is out of scope.
- Features that fall outside this boundary are not thereby assigned anywhere in this
  project.
- The scope is not frozen forever. DeltaTrack may expand, but only on demonstrated user
  demand.
