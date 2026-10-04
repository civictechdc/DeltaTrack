# Security Policy

DeltaTrack compares versions of U.S. bills. Its security-relevant
surface is the hosted compare service — the public `/api/compare` upload endpoint
(`web/`), the front-end it serves (`web/webapp/`) — and the PDF/XML parsers behind
it, which process documents from untrusted sources.

## Reporting a vulnerability

**Please do not open a public issue for a vulnerability.** A public report
discloses the flaw before a fix exists. Instead, use GitHub's private reporting:

- [Report a vulnerability](https://github.com/civictechdc/DeltaTrack/security/advisories/new)
  (also reachable via the repo's **Security** tab → "Report a vulnerability").

You'll get an acknowledgment within a week. This is a small volunteer-run [Civic
Tech](https://www.civictechdc.org/) project, so fixes ship on a best-effort timeline; we'll keep you updated in
the advisory thread and credit you in the fix unless you prefer otherwise.

## Scope

In scope: anything exploitable through the hosted service, through crafted bill
files (PDF/XML) fed to the parsers or CLI tools, leaked credentials, and CI or
GitHub Actions workflow weaknesses in this repository.

If you're not sure whether something qualifies, report it privately anyway —
a false alarm costs a reply; a public disclosure can't be taken back.
