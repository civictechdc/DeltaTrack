"""A bounded, abstaining per-figure experiment; not a product classifier.

Ranges address the XML serializer's full text, not raw XML byte offsets. The heading
path and observation ordinal come from the parser, never from financial wording. The
heading path is where a figure sits, not its account: a general provision can rescind from
accounts it names only in its text, so `text_account` holds the name the text gives, or
None, and `text_account_ordinal` the one node in the same parse that name resolves to, or
None. `fiscal_year` is the funding year of the money a figure adds, removes or refers
to, and None unless the text states it. `independent_evidence` reports implemented checks
only; manual source inspection is recorded in the README.
"""

import re
from dataclasses import dataclass

_FIGURE = re.compile(r"\$\d[\d,]*(?:\.\d+)?(?<![,.])")
_PROVISO = re.compile(r"\bProvided(?:\s+further)?,?\s+That\b", re.IGNORECASE)
_OCTOBER = re.compile(r"(?:available|availability)\s+on\s+October\s+1,?\s+(\d{4})", re.IGNORECASE)
_SEPTEMBER = re.compile(r"remain\s+available\s+until\s+September\s+30,?\s+(\d{4})", re.IGNORECASE)
_REFERENCE = re.compile(r"of\s+the\s*$", re.IGNORECASE)
_RESCINDED = re.compile(r"^\s+is\s+hereby\s+rescinded\b", re.IGNORECASE)
_ACCOUNT_LIST = re.compile(
    r"\bhereby\s+rescinded\s+from\s+the\s+following\s+accounts\s+in\s+the\s+amounts\s+specified:", re.IGNORECASE
)
# One list item names one account and one figure: "Name, $X;" or "and Name, $X:".
_ACCOUNT_ITEM = re.compile(
    r"\s*(?:and\s+)?(?P<account>[^;:$]+?),\s+(?P<figure>\$\d[\d,]*(?:\.\d+)?)(?<![,.])(?P<end>[;:])"
)
# Names join their headings with an em dash ("Veterans Health Administration—Medical
# Services") or a comma ("Veterans Health Administration, Medical Services").
_SEPARATOR = re.compile(r"\s*[—,]\s*")


@dataclass(frozen=True)
class Figure:
    amount: str
    start: int
    end: int
    ordinal: int
    element_id: str
    heading_path: tuple[str, ...]
    source_sha256: str
    parser_revision: str
    effect: str
    role: str
    fiscal_year: int | None
    availability_end_year: int | None
    text_account: str | None
    text_account_span: tuple[int, int] | None
    text_account_ordinal: int | None
    text_account_heading_path: tuple[str, ...] | None
    independent_evidence: str = "not_checked"


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node["children"])


def _listed_accounts(clause):
    """Map each listed figure's offset to its account's span; any malformed item ends the list."""
    lead = _ACCOUNT_LIST.search(clause)
    if not lead:
        return {}
    listed, position = {}, lead.end()
    while item := _ACCOUNT_ITEM.match(clause, position):
        listed[item.start("figure")] = item.span("account")
        position = item.end()
        if item.group("end") == ":":
            return listed
    return {}


def _account_key(text):
    return _SEPARATOR.sub("—", " ".join(text.split())).casefold()


def resolve_text_account(bill, name):
    """Return the ordinal of the one node whose trailing whole headings spell `name`, else None.

    Only case, whitespace and the separator are normalised. A heading's own commas count as
    separators on both sides, so "Military Construction, Defense-Wide" matches that heading,
    but a match never starts inside a heading.
    """
    key = _account_key(name)
    hits = [
        ordinal
        for ordinal, node in enumerate(bill.nodes)
        if any(_account_key("—".join(node.display_path[-k:])) == key for k in range(1, len(node.display_path) + 1))
    ]
    return hits[0] if len(hits) == 1 else None


def inspect_node(bill, ordinal, full_text, tree, node_ids, *, source_sha256, parser_revision):
    """Inspect one node's body, retaining every figure even when interpretation abstains."""
    source = bill.nodes[ordinal]
    [target] = [node for node in _walk(tree) if node["id"] == node_ids[ordinal]]
    span = target["body_span"]
    if span is None:
        raise ValueError("source node has no body span")
    body_start, body_end = span["start"], span["end"]
    body = full_text[body_start:body_end]
    boundaries = [0, *(m.end() for m in _PROVISO.finditer(body)), len(body)]
    rows = []
    for clause_index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        clause = body[start:end]
        prior_year = None
        figures = list(_FIGURE.finditer(clause))
        listed = _listed_accounts(clause)
        for figure in figures:
            effect, role, year = "unresolved", "unknown", None
            availability_end = account_span = None
            before, after = clause[: figure.start()], clause[figure.end() :]
            date = _OCTOBER.search(after)
            if figure.start() in listed:
                # "Prior appropriations Acts" names no funding year, so none is assigned.
                effect, role, account_span = "removes", "rescission", listed[figure.start()]
            elif clause_index == 0 and len(figures) == 1 and clause.lstrip().startswith("For ") and date:
                effect, role, year = "adds", "appropriation", int(date.group(1)) + 1
                last = _SEPTEMBER.search(after)
                availability_end = int(last.group(1)) if last else None
            elif _REFERENCE.search(before) and date and "previously appropriated" in after.lower():
                effect, role, year = "neither", "prior_appropriation_reference", int(date.group(1)) + 1
                prior_year = year
            elif _RESCINDED.match(after):
                effect, role, year = "removes", "rescission", prior_year
            elif "of the amount made available" in before.lower() and "shall remain available" in after.lower():
                date = _OCTOBER.search(before)
                if date:
                    effect, role, year = "neither", "availability", int(date.group(1)) + 1
                    last = _SEPTEMBER.search(after)
                    availability_end = int(last.group(1)) if last else None
            text_account = clause[slice(*account_span)] if account_span else None
            account_ordinal = resolve_text_account(bill, text_account) if text_account else None
            rows.append(
                Figure(
                    amount=figure.group(),
                    start=body_start + start + figure.start(),
                    end=body_start + start + figure.end(),
                    ordinal=ordinal,
                    element_id=source.element_id,
                    heading_path=source.display_path,
                    source_sha256=source_sha256,
                    parser_revision=parser_revision,
                    effect=effect,
                    role=role,
                    fiscal_year=year,
                    availability_end_year=availability_end,
                    text_account=text_account,
                    text_account_span=(
                        (body_start + start + account_span[0], body_start + start + account_span[1])
                        if account_span
                        else None
                    ),
                    text_account_ordinal=account_ordinal,
                    text_account_heading_path=(
                        bill.nodes[account_ordinal].display_path if account_ordinal is not None else None
                    ),
                )
            )
    return rows
