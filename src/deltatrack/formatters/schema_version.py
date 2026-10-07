"""The canonical diff document's schema version, on its own so both sides can read it.

The producers stamp it on every document (`formatters.canonical`); the viewer checks a
document's major against it before reading (`formatters.canonical_view`). It lives in
neither, so reading it loads nothing else: the viewer must not load the producers, which
load the parsers, the differs and the PDF library (#801). Versioning rules:
`schema/canonical-diff.md`.
"""

SCHEMA_VERSION = "3.0"
