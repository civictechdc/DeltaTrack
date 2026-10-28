"""A bounded, abstaining per-figure experiment; not a product classifier.

Ranges address the XML serializer's full text, not raw XML byte offsets. The account
and observation ordinal come from the parser, never from financial wording.
"""

import re
from dataclasses import dataclass

_FIGURE = re.compile(r"\$\d[\d,]*(?:\.\d+)?(?<![,.])")
_PROVISO = re.compile(r"\bProvided(?:\s+further)?,?\s+That\b", re.IGNORECASE)
_OCTOBER = re.compile(r"(?:available|availability)\s+on\s+October\s+1,?\s+(\d{4})", re.IGNORECASE)
_SEPTEMBER = re.compile(r"remain\s+available\s+until\s+September\s+30,?\s+(\d{4})", re.IGNORECASE)
_REFERENCE = re.compile(r"of\s+the\s*$", re.IGNORECASE)
_RESCINDED = re.compile(r"^\s+is\s+hereby\s+rescinded\b", re.IGNORECASE)


@dataclass(frozen=True)
class Figure:
    amount: str
    start: int
    end: int
    ordinal: int
    element_id: str
    account_path: tuple[str, ...]
    source_sha256: str
    parser_revision: str
    effect: str
    role: str
    fiscal_year: int | None
    availability_end_year: int | None
    independent_evidence: str = "not_checked"


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node["children"])


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
        for figure in figures:
            effect, role, year = "unresolved", "unknown", None
            availability_end = None
            before, after = clause[: figure.start()], clause[figure.end() :]
            date = _OCTOBER.search(after)
            if clause_index == 0 and len(figures) == 1 and clause.lstrip().startswith("For ") and date:
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
            rows.append(
                Figure(
                    amount=figure.group(),
                    start=body_start + start + figure.start(),
                    end=body_start + start + figure.end(),
                    ordinal=ordinal,
                    element_id=source.element_id,
                    account_path=source.display_path,
                    source_sha256=source_sha256,
                    parser_revision=parser_revision,
                    effect=effect,
                    role=role,
                    fiscal_year=year,
                    availability_end_year=availability_end,
                )
            )
    return rows
