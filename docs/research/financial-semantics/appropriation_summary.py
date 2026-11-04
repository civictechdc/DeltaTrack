"""Selected appropriation candidates for the research notebook, with exclusions visible.

Full source heading paths identify groups. A named recipient in an explicit additional-
amount list stays attached to its source section; it does not create a structural heading
or get guessed into an account elsewhere in the bill (ADR 0018).

These are classifier candidates, not validated budget authority. Multi-figure clauses
outside the supported list form abstain; they never contribute their first figure alone.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from html import escape

from classify_bill import DOLLAR, classify_text, non_cap_amounts, split_clauses

# This wording appropriates additional amounts to the listed recipients. Require the
# entire list to parse, so a malformed or qualifying item cannot leave a plausible subtotal.
_ADDITIONAL_LIST = re.compile(
    r"^For an additional amount for the accounts and (?:in the )?amounts specified\b[^$:]*:\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_NAMED_AMOUNT = re.compile(r"([^$;:]+?),\s*\$([\d,]+(?:\.\d+)?)\s*")


@dataclass(frozen=True)
class AppropriationEntry:
    node_idx: int
    clause_idx: int
    heading_path: tuple[str, ...]
    recipient: str
    amount: Decimal


@dataclass(frozen=True)
class ExcludedClause:
    node_idx: int
    clause_idx: int
    heading_path: tuple[str, ...]
    text: str
    reason: str


@dataclass(frozen=True)
class AppropriationSummary:
    entries: tuple[AppropriationEntry, ...]
    excluded: tuple[ExcludedClause, ...]


def build_appropriation_summary(tree):
    entries, excluded = [], []
    for node_idx, node in enumerate(tree.nodes):
        path = tuple(node.display_path)
        node_label = classify_text(node.body_text)
        for clause_idx, (text, level) in enumerate(split_clauses(node.body_text)):
            if not DOLLAR.search(text):
                continue
            label = node_label if level == "primary" else classify_text(text)
            if label != "appropriation":
                if label == "unknown":
                    excluded.append(
                        ExcludedClause(node_idx, clause_idx, path, text, "Unclassified dollar-bearing clause")
                    )
                continue
            listing = _ADDITIONAL_LIST.fullmatch(text)
            if listing:
                parts = re.split(r";\s*(?:and\s+)?", listing.group(1).rstrip(" :;"), flags=re.IGNORECASE)
                matches = [_NAMED_AMOUNT.fullmatch(part.strip()) for part in parts]
                if not all(matches):
                    excluded.append(
                        ExcludedClause(node_idx, clause_idx, path, text, "Unresolved additional-amount list")
                    )
                    continue
                candidates = [(m.group(1).strip(), m.group(2)) for m in matches]
            elif len(clean := non_cap_amounts(text)) == 1:
                candidates = [("", clean[0])]
            else:
                excluded.append(ExcludedClause(node_idx, clause_idx, path, text, "No unambiguous appropriation amount"))
                continue
            for recipient, digits in candidates:
                entries.append(
                    AppropriationEntry(node_idx, clause_idx, path, recipient, Decimal(digits.replace(",", "")))
                )
    return AppropriationSummary(tuple(entries), tuple(excluded))


def _money(amount):
    return f"${amount:,.0f}" if amount == amount.to_integral_value() else f"${amount:,.2f}"


def build_summary_html(summary):
    """Render selected subtotals and the clauses left out, without claiming completeness."""
    groups = {
        row.heading_path[:1]: {}
        for row in sorted((*summary.entries, *summary.excluded), key=lambda row: (row.node_idx, row.clause_idx))
    }
    for entry in summary.entries:
        heading = entry.heading_path[:1]
        accounts = groups[heading]
        key = (entry.heading_path, entry.recipient)
        accounts[key] = accounts.get(key, Decimal(0)) + entry.amount
    rows = []
    for group_id, (heading, accounts) in enumerate(groups.items()):
        incomplete = any(c.heading_path[:1] == heading for c in summary.excluded)
        suffix = " (Incomplete)" if incomplete else ""
        label = escape(" > ".join(heading) or "No heading")
        subtotal = _money(sum(accounts.values(), Decimal(0))) if accounts else "Not selected"
        heading_html = (
            f'<button type="button" aria-expanded="false" onclick="toggleHeading(this)" '
            f'data-group="{group_id}">{label}{suffix}</button>'
            if accounts
            else f"{label}{suffix}"
        )
        rows.append(f'<tr class="heading-row"><td>{heading_html}</td><td class="sum-amount">{subtotal}</td></tr>')
        for (path, recipient), amount in accounts.items():
            label = " > ".join((*path[1:], recipient) if recipient else path[1:]) or "No heading"
            rows.append(
                f'<tr class="summary-account summary-group-{group_id}" hidden>'
                f'<td>{escape(label)}</td><td class="sum-amount">{_money(amount)}</td></tr>'
            )
    exclusions = []
    for clause in summary.excluded:
        path = escape(" > ".join(clause.heading_path) or "No heading")
        exclusions.append(f"<li><strong>{path}: {escape(clause.reason)}</strong><p>{escape(clause.text)}</p></li>")
    omitted = (
        f'<details class="summary-excluded"><summary>Excluded clauses ({len(exclusions)})</summary>'
        + "<ul>"
        + "".join(exclusions)
        + "</ul></details>"
        if exclusions
        else ""
    )
    return (
        "<script>function toggleHeading(button) {"
        'const open = button.getAttribute("aria-expanded") !== "true";'
        'button.setAttribute("aria-expanded", String(open));'
        'document.querySelectorAll(".summary-group-" + button.dataset.group).forEach(row => {row.hidden = !open;});'
        "}</script>"
        "<style>.summary-wrap {margin-bottom:24px} .summary-wrap > summary {font-size:20px;cursor:pointer}"
        "#summary-table {width:100%;border-collapse:collapse;margin:12px 0}"
        "#summary-table td,#summary-table th {padding:8px 12px;border-bottom:1px solid #e3ddd7;text-align:left}"
        "#summary-table th:last-child {text-align:right}"
        "#summary-table .sum-amount {text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}"
        ".heading-row {background:#f0ede9;font-weight:600}"
        ".heading-row button {font:inherit;color:inherit;border:0;background:none;cursor:pointer;text-align:left}"
        '.heading-row button::before {content:"▸ ";color:#777}'
        '.heading-row button[aria-expanded="true"]::before {content:"▾ "}'
        ".summary-account td:first-child {padding-left:28px} .summary-note {font-size:13px;color:#555}"
        ".summary-excluded {margin:12px 0} .summary-excluded li {margin:8px 20px}</style>"
        '<details class="summary-wrap" open><summary>Appropriations Summary (Research)</summary>'
        '<p class="summary-note">Selected classifier candidates only. These are not validated budget totals; '
        "they can include amounts for different fiscal years. Unresolved clauses are excluded, and their "
        "heading groups are marked Incomplete. Other classification errors may remain.</p>"
        '<table id="summary-table"><thead><tr><th>Source heading / named recipient</th>'
        "<th>Selected subtotal</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + ("" if groups else "<p>No appropriation candidates selected.</p>")
        + omitted
        + "</details>"
    )
