"""The report's three financial views, rendered from the canonical ``financial`` field (ADR 0023).

- **Financials – Version A / B**: one version's ledger as the research notebook's financial
  report showed it (`docs/research/financial-semantics/financial_118_hr_4366.html`): one row
  per money-bearing section with its type and amount, a key of the types, a sort bar, a
  "review" badge where a clause holds more than one figure, and a row that opens onto
  the section's clauses and its text, highlighted clause by clause.
- **Inferred Financial Comparison**: one row per change the differ already reported whose
  section holds money on either side, Version A's amount over Version B's, and the
  difference in money given out. A row opens onto that change's card from the Changes view,
  so the comparison has no matching of its own.

Money given out means an appropriation or a rescission outside an amendment to another law
(``financial.given_out``); everything else is shown and never summed.
"""

from __future__ import annotations

import json
from html import escape

from deltatrack.financial import TYPES, given_out

__all__ = ["FINANCIAL_CSS", "FINANCIAL_JS", "financial_toggle_buttons", "financial_views_html", "has_financial"]

_TYPE_DESCRIPTIONS = {
    "appropriation": "Funds directly allocated to an agency or program",
    "transfer": "Funds moved between accounts",
    "rescission": "Previously appropriated funds clawed back",
    "restriction": "Prohibition on how funds may be used",
    "authorization": "Congressional authorization to request an appropriation; funds not yet available for obligation",
    "cap": "Upper limit on spending for a specific purpose",
    "earmark": "Funds designated for a recipient listed in a table",
    "availability": "Time extension specifying when funds may be spent",
    "sub_allocation": "Portion of funds reserved for a specific use",
    "directive": "Mandatory action required of an agency",
    "fee": "Charge imposed on individuals or entities",
    "unknown": "Could not be automatically classified",
}

_SIDES = {"v1": "A", "v2": "B"}


def has_financial(canonical: dict | None) -> bool:
    """The views render only when the document carries a ledger for both sides."""
    fin = (canonical or {}).get("financial") or {}
    return bool(fin.get("v1") and fin.get("v2"))


def _money(value) -> str:
    if value is None:
        return "—"
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _signed(value) -> str:
    if value == 0:
        return "$0"
    return ("+" if value > 0 else "−") + _money(abs(value))


def _type_badge(kind: str) -> str:
    kind = kind if kind in _TYPE_DESCRIPTIONS else "unknown"
    return f'<span class="fin-type fin-type--{kind}">{escape(kind.replace("_", " "))}</span>'


def _label(section: dict) -> str:
    """The row's name, as the notebook named it: the last two breadcrumb levels."""
    return " > ".join(label for label, _level in section["path"][-2:])


def _about_figures_html(canonical: dict) -> str:
    """The information alert at the top of each financial view: what these figures are and
    are not, and which classifier typed them. The version is the one the document records,
    so a saved report names the rules that produced it, not whichever rules run now."""
    version = escape(str((canonical.get("financial") or {}).get("classifier") or "unknown"))
    return (
        '<div class="fin-alert" role="note">'
        '<svg class="fin-alert__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
        '<circle cx="12" cy="12" r="9.5"></circle><line x1="12" y1="11" x2="12" y2="16.5"></line>'
        '<circle class="fin-alert__dot" cx="12" cy="7.6" r="0.6"></circle></svg>'
        "<div><strong>These classifications are not authoritative.</strong> DeltaTrack assigns each "
        "dollar amount a type (appropriation, rescission, cap, and so on) from the wording of its "
        "clause. Audit and verify every value against the bill text before you rely on it. The "
        "classifications are subject to change as the application is improved. Certain sections "
        "and amounts may have been missed and not shown at all. Send feedback, "
        "suggestions, screenshots or issues to the #congressional-tech channel of the "
        '<a href="https://www.civictechdc.org/slack" target="_blank" rel="noopener noreferrer">'
        "Civic Tech DC Slack</a>."
        f'<span class="fin-alert__version">Pattern classifier version {version}.</span></div>'
        "</div>"
    )


def _version_label(canonical: dict, side: str) -> str:
    return (canonical.get("versions") or {}).get(side, {}).get("label") or ""


# ---------- Version A / B --------------------------------------------------------------------


#: The views' warning mark: an inline SVG triangle in the text's own colour, not the
#: U+26A0 glyph, which fonts draw inconsistently (some as a colour emoji) and screen
#: readers announce as "warning sign" mid-sentence. Decorative (`aria-hidden`): every use
#: carries its meaning in words beside it, or in `_REVIEW_MARK`'s label.
_WARNING_ICON = (
    '<svg class="fin-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M12 3.5 2.5 20h19z"></path><line x1="12" y1="10" x2="12" y2="14.5"></line>'
    '<circle class="fin-icon__dot" cx="12" cy="17.2" r="0.6"></circle></svg>'
)
#: The mark alone, where a row has no room for the word (the comparison's A / B lines).
_REVIEW_MARK = (
    f'<span class="fin-review" role="img" aria-label="needs review" title="Needs review">{_WARNING_ICON}</span>'
)


def _coverage_html(ledger: dict) -> str:
    """Every figure in the version's text is on this page, or the page says how many are not."""
    total = ledger.get("amounts_in_text", 0)
    shown = sum(len(c["amounts"]) for s in ledger["sections"] for c in s["clauses"])
    if shown >= total:
        return (
            f'<p class="fin-coverage">All {total:,} dollar figures found in this version\'s text are shown below.</p>'
        )
    missing = total - shown
    return (
        f'<p class="fin-coverage fin-coverage--gap" role="status">{_WARNING_ICON}'
        f"{missing:,} of {total:,} dollar amounts in this version could not be placed in a section "
        "and are not shown here. They are in the Full bill view.</p>"
    )


def _amended_note_html(ledger: dict) -> str:
    n = sum(1 for s in ledger["sections"] for c in s["clauses"] for a in c["amounts"] if a["in_amended_law"])
    if not n:
        return ""
    return (
        f'<p class="fin-note">{n:,} amounts sit inside text this bill writes into other laws. '
        'They are marked <span class="fin-chip fin-chip--law">in amended law</span> and are never '
        "counted as money given out.</p>"
    )


def _legend_html(ledger: dict) -> str:
    counts: dict[str, int] = {}
    for section in ledger["sections"]:
        for clause in section["clauses"]:
            counts[clause["type"]] = counts.get(clause["type"], 0) + 1
    items = []
    for kind in TYPES:
        n = counts.get(kind, 0)
        count = f'<span class="fin-key__count">({n})</span>' if n else '<span class="fin-key__count"></span>'
        items.append(
            f'<div class="fin-key__item">{_type_badge(kind)}{count}'
            f'<span class="fin-key__desc">{escape(_TYPE_DESCRIPTIONS[kind])}</span></div>'
        )
    return f'<div class="fin-key"><div class="fin-key__title">Category key</div>{"".join(items)}</div>'


def _rows_html(side: str, ledger: dict) -> str:
    rows = []
    for gid, section in enumerate(ledger["sections"]):
        clauses = section["clauses"]
        first = clauses[0]
        review = any(c["needs_review"] for c in clauses)
        badges = ""
        if len(clauses) > 1:
            badges += f'<span class="fin-count">({len(clauses) - 1})</span>'
        if review:
            badges += f'<span class="fin-review">{_WARNING_ICON}review</span>'
        if "may_hold_several_sections" in section["flags"]:
            badges += (
                '<span class="fin-review" title="The text holds a further section heading the '
                'parser did not start a row for, so this row may carry more than one section">'
                f"{_WARNING_ICON}may hold more than one section</span>"
            )
        if first["in_amended_law"]:
            badges += '<span class="fin-chip fin-chip--law">in amended law</span>'
        amount = first["amount"] if first["amount"] is not None else 0
        rows.append(
            f'<tr class="fin-row" data-group="{gid}" data-amount="{amount}" data-review="{1 if review else 0}"'
            f' tabindex="0" aria-expanded="false">'
            f'<td class="fin-row__name">{escape(_label(section))}{badges}</td>'
            f"<td>{_type_badge(first['type'])}</td>"
            f'<td class="fin-amount">{_money(first["amount"])}</td></tr>'
        )
        items = "".join(
            f'<div class="fin-clause">{_type_badge(c["type"])}'
            f'<span class="fin-clause__amount">{_money(c["amount"])}</span>'
            + ('<span class="fin-chip fin-chip--law">in amended law</span>' if c["in_amended_law"] else "")
            + "</div>"
            for c in clauses
        )
        pieces = escape(json.dumps(section["pieces"], separators=(",", ":")))
        rows.append(
            f'<tr class="fin-detail" data-group="{gid}" data-find-reveal hidden><td colspan="3">'
            '<div class="fin-detail__inner">'
            f'<div class="fin-detail__amounts">{items}</div>'
            f'<div class="fin-detail__text" data-side="{side}" data-pieces="{pieces}"></div>'
            f"</div></td></tr>"
        )
    return "".join(rows)


def _version_view_html(canonical: dict, side: str) -> str:
    ledger = canonical["financial"][side]
    letter = _SIDES[side]
    label = _version_label(canonical, side)
    table = (
        f'<div class="fin-scroll"><table class="fin-table" data-side="{side}"><thead><tr>'
        '<th class="fin-col-name">Account</th><th class="fin-col-type">Type</th>'
        '<th class="fin-col-amount">Amount</th></tr></thead>'
        f"<tbody>{_rows_html(side, ledger)}</tbody></table></div>"
        if ledger["sections"]
        else '<p class="fin-empty">No dollar amounts found in this version.</p>'
    )
    return (
        f'<div class="view view-fin-{letter.lower()}" hidden>'
        '<div class="fin-head">'
        f"<h2>Financials – Version {letter}</h2>"
        f'<button class="export-btn fin-export" type="button" data-side="{side}">Export Inferred Financials</button>'
        "</div>"
        f'<div class="fin-version">Version {letter}: {escape(label)}</div>'
        f"{_about_figures_html(canonical)}"
        f"{_coverage_html(ledger)}{_amended_note_html(ledger)}{_legend_html(ledger)}"
        "<h3>Financial Summary</h3>"
        '<div class="fin-sort" role="group" aria-label="Sort"><span>Sort:</span>'
        '<button class="fin-sort__btn is-active" type="button" data-sort="default">Default</button>'
        '<button class="fin-sort__btn" type="button" data-sort="amount">Amount ↓</button>'
        '<button class="fin-sort__btn" type="button" data-sort="review">Needs review first</button>'
        f"</div>{table}</div>"
    )


# ---------- Comparison -----------------------------------------------------------------------


def _overlapping_groups(sections: list[dict], span: dict | None) -> list[int]:
    """Positions of the ledger sections a change's span overlaps."""
    if not span:
        return []
    return [i for i, s in enumerate(sections) if s["span"][0] < span["end"] and span["start"] < s["span"][1]]


def _side_lines(sections: list[dict]) -> list[str]:
    return [
        _type_badge(s["clauses"][0]["type"])
        + (f" {_REVIEW_MARK}" if any(c["needs_review"] for c in s["clauses"]) else "")
        for s in sections
    ]


def _given(sections: list[dict]):
    values = [given_out(s["clauses"][0]) for s in sections]
    values = [v for v in values if v is not None]
    return sum(values) if values else None


def comparison_rows(canonical: dict) -> list[dict]:
    """The comparison's rows: each reported change whose section holds money on either side.

    ``change_index`` is the change's position in ``canonical["changes"]``, which is also its
    card's ``id="change-{index}"`` in the Changes view. ``a_groups`` / ``b_groups`` are the
    sections' positions in that version's ledger, which are their rows' ``data-group`` in
    the Version A / B views (the comparison's links open them there).
    """
    fin = canonical["financial"]
    rows = []
    for index, change in enumerate(canonical.get("changes") or []):
        span = change.get("full_text_span") or {}
        a_groups = _overlapping_groups(fin["v1"]["sections"], span.get("v1"))
        b_groups = _overlapping_groups(fin["v2"]["sections"], span.get("v2"))
        if a_groups or b_groups:
            rows.append(
                {
                    "change_index": index,
                    "change": change,
                    "a": [fin["v1"]["sections"][g] for g in a_groups],
                    "b": [fin["v2"]["sections"][g] for g in b_groups],
                    "a_groups": a_groups,
                    "b_groups": b_groups,
                }
            )
    return rows


def _jump_links_html(row: dict) -> str:
    """Links from a comparison row to its sections in Version A and Version B, where every
    clause, tag and amount is laid out; the comparison itself stays one line per side."""
    links = []
    for side, letter, groups in (("v1", "A", row["a_groups"]), ("v2", "B", row["b_groups"])):
        if not groups:
            links.append(f'<span class="fin-jump fin-jump--none">not in Version {letter}</span>')
            continue
        for n, group in enumerate(groups):
            text = f"Version {letter} section" + (f" {n + 1}" if len(groups) > 1 else "")
            links.append(
                f'<button class="fin-jump" type="button" data-side="{side}" data-group="{group}">{text}</button>'
            )
    return f'<span class="fin-jumps">{"".join(links)}</span>'


def _location_html(change: dict) -> str:
    path = (change.get("path") or {}).get("v2") or (change.get("path") or {}).get("v1") or []
    if not path:
        return '<span class="fin-loc">(location not resolved)</span>'
    parents = " › ".join(escape(p) for p in path[:-1])
    return f'<span class="fin-loc">{escape(path[-1])}<small>{parents}</small></span>'


def _stack_html(row: dict) -> str:
    cells = []
    for letter, sections, cls in (("A", row["a"], "old"), ("B", row["b"], "new")):
        if not sections:
            cells.append(
                f'<span class="fin-ab fin-ab--{letter.lower()}">{letter}</span><span></span>'
                f'<span class="fin-stack__none">not in {letter}</span>'
            )
            continue
        for tag, section in zip(_side_lines(sections), sections):
            cells.append(
                f'<span class="fin-ab fin-ab--{letter.lower()}">{letter}</span>'
                f'<span class="fin-stack__tag">{tag}</span>'
                f'<span class="fin-stack__amt fin-stack__amt--{cls}">{_money(section["clauses"][0]["amount"])}</span>'
            )
    return f'<div class="fin-stack">{"".join(cells)}</div>'


def _difference(row: dict) -> tuple[float | None, str]:
    """(B minus A in money given out, or None when neither side gives any out; its note)."""
    a, b = _given(row["a"]), _given(row["b"])
    if a is None and b is None:
        return None, "not given-out money"
    delta = (b or 0) - (a or 0)
    return delta, "text changed" if delta == 0 else ""


def _difference_html(row: dict) -> tuple[str, float]:
    delta, note = _difference(row)
    if delta is None:
        return f'<span class="fin-same">—<small>{note}</small></span>', 0
    if delta == 0:
        return f'<span class="fin-same">$0<small>{note}</small></span>', 0
    cls = "fin-up" if delta > 0 else "fin-down"
    return f'<span class="{cls}">{_signed(delta)}</span>', delta


def _amended_figures(sections: list[dict]) -> list:
    return [a["value"] for s in sections for c in s["clauses"] for a in c["amounts"] if a["in_amended_law"]]


def comparison_export_rows(canonical: dict) -> list[dict]:
    """The comparison table as data, one entry per row, for its CSV export.

    Built from the same `comparison_rows` and `_difference` the table renders, so the export
    cannot disagree with the page. Carries no bill text: the page adds each change's words
    from the embedded document (`changes[change_index].text`).
    """
    out = []
    for row in comparison_rows(canonical):
        change = row["change"]
        path = (change.get("path") or {}).get("v2") or (change.get("path") or {}).get("v1") or []
        delta, note = _difference(row)
        entry = {
            "change_index": row["change_index"],
            "change_type": change["change_type"],
            "location": " > ".join(path),
            "difference": delta,
            "difference_note": note,
        }
        for key, sections in (("a", row["a"]), ("b", row["b"])):
            entry[key] = {
                "sections": [_label(s) for s in sections],
                "types": [s["clauses"][0]["type"] for s in sections],
                "amounts": [s["clauses"][0]["amount"] for s in sections],
                "given_out": _given(sections),
                "needs_review": any(c["needs_review"] for s in sections for c in s["clauses"]),
                "flags": sorted({f for s in sections for f in s["flags"]}),
                "amended_law_amounts": _amended_figures(sections),
            }
        out.append(entry)
    return out


def _amended_rows_html(rows: list[dict]) -> str:
    items = []
    for row in rows:

        def figures(sections):
            return ", ".join(_money(v) for v in _amended_figures(sections))

        a, b = figures(row["a"]), figures(row["b"])
        if (a or b) and a != b:
            items.append(
                f"<tr><td>{_location_html(row['change'])}</td>"
                f'<td><span class="badge badge-{escape(row["change"]["change_type"])}">'
                f"{escape(row['change']['change_type'])}</span></td>"
                f"<td>A: {a or '—'} &rarr; B: {b or '—'}</td></tr>"
            )
    if not items:
        return ""
    return (
        '<div class="fin-subhead">Amounts in laws this bill amends (not counted above)</div>'
        f'<table class="fin-table fin-table--plain"><tbody>{"".join(items)}</tbody></table>'
    )


def _comparison_view_html(canonical: dict) -> str:
    rows = comparison_rows(canonical)
    body, counts, net = [], {}, 0
    for row in rows:
        ct = row["change"]["change_type"]
        counts[ct] = counts.get(ct, 0) + 1
        diff_html, delta = _difference_html(row)
        net += delta
        i = row["change_index"]
        body.append(
            f'<tr class="fin-crow" data-change="change-{i}" tabindex="0" aria-expanded="false">'
            f'<td><span class="fin-caret">▸</span>{_location_html(row["change"])}{_jump_links_html(row)}</td>'
            f'<td><span class="badge badge-{escape(ct)}">{escape(ct)}</span></td>'
            f"<td>{_stack_html(row)}</td>"
            f'<td class="fin-amount">{diff_html}</td></tr>'
            f'<tr class="fin-cdetail" data-change="change-{i}" data-find-reveal hidden><td colspan="4"></td></tr>'
        )
    summary = [f"<span>Sections with money changes <b>{len(rows)}</b></span>"]
    summary += [f"<span>{escape(k.capitalize())} <b>{v}</b></span>" for k, v in counts.items()]
    net_cls = "fin-up" if net > 0 else ("fin-down" if net < 0 else "fin-same")
    summary.append(f'<span>Net change in money given out <b class="{net_cls}">{_signed(net)}</b></span>')
    a_label, b_label = _version_label(canonical, "v1"), _version_label(canonical, "v2")
    table = (
        '<div class="fin-scroll"><table class="fin-table fin-table--compare"><thead><tr>'
        "<th>Location in the bill</th><th>Text block change</th>"
        '<th>Version A &rarr; Version B</th><th class="fin-col-amount">Difference</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>"
        if rows
        else '<p class="fin-empty">No change touches a section that holds money.</p>'
    )
    # `</` is escaped so no bill label can close the script element early.
    export_json = json.dumps(comparison_export_rows(canonical), ensure_ascii=False, separators=(",", ":"))
    export_json = export_json.replace("</", "<\\/")
    return (
        '<div class="view view-fin-compare" hidden>'
        '<div class="fin-head">'
        "<h2>Inferred Financial Comparison</h2>"
        '<button class="export-btn fin-export-compare" type="button">Export Comparison</button>'
        "</div>"
        f'<script type="application/json" id="fin-compare-data">{export_json}</script>'
        f'<div class="fin-version">Version A: {escape(a_label)} &rarr; Version B: {escape(b_label)}</div>'
        f"{_about_figures_html(canonical)}"
        '<p class="fin-explain">Every change in the Changes view whose section holds money, with the same '
        "change words. The difference counts money given out or taken back only. Open a row for that "
        "change's word diff.</p>"
        f'<div class="fin-card"><div class="fin-summary">{"".join(summary)}</div>{table}'
        f"{_amended_rows_html(rows)}</div></div>"
    )


def financial_views_html(canonical: dict) -> str:
    """All three views, each a hidden ``.view`` the view toggle reveals."""
    if not has_financial(canonical):
        return ""
    return _version_view_html(canonical, "v1") + _version_view_html(canonical, "v2") + _comparison_view_html(canonical)


def financial_toggle_buttons() -> str:
    """The three view-toggle buttons, after Changes / Full bill. The comparison comes first:
    like Changes and Full bill it is about the pair, where Version A / B are one version each."""
    return "".join(
        f'<button class="view-toggle__btn" data-view="{view}" role="tab" aria-selected="false">{label}</button>'
        for view, label in (
            ("fin-compare", "Inferred Financial Comparison"),
            ("fin-a", "Financials – Version A"),
            ("fin-b", "Financials – Version B"),
        )
    )


# The views' styles and script, which `diff_html.format_diff_html` includes in every report.
# They live here with the money vocabulary they are written in (type names such as
# `rescission`), so the renderer module itself stays outside the financial layer (ADR 0018).
FINANCIAL_CSS = """\
/* Financial views (ADR 0023): Version A / B, and the comparison */
/* The three financial tabs use the full window on a wide screen: their tables are columns,
   cramped at the report's 940px reading width. Changes and Full bill keep that width (their
   cards are prose). `body[data-active-view]` is set by the report's view switch. */
@media (min-width: 821px) {
  body[data-active-view="fin-a"] .main, body[data-active-view="fin-b"] .main,
  body[data-active-view="fin-compare"] .main {
    max-width: none; padding-right: 10%; }
}
.fin-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.view-fin-a h2, .view-fin-b h2, .view-fin-compare h2 { font-size: 20px; margin-bottom: 2px; }
.fin-version { color: var(--muted-foreground); font-size: 14px; margin-bottom: 12px; }
.fin-coverage, .fin-note, .fin-explain { font-size: 13px; color: var(--muted-foreground); margin-bottom: 10px; }
/* "About these figures": an information alert, left-accented, above each view's content. */
.fin-alert { display: flex; gap: 12px; align-items: flex-start; margin: 4px 0 16px; padding: 12px 16px;
  background: var(--info); color: var(--info-foreground); border: 1px solid var(--info);
  border-left: 4px solid var(--info-foreground); border-radius: var(--radius); font-size: 14px; line-height: 1.55; }
.fin-alert__icon { flex: none; width: 20px; height: 20px; margin-top: 1px; fill: none;
  stroke: var(--info-foreground); stroke-width: 2; stroke-linecap: round; }
.fin-alert__dot { fill: var(--info-foreground); }
.fin-alert a { color: inherit; font-weight: 600; text-decoration: underline; }
.fin-alert__version { display: block; margin-top: 4px; font-size: 12px; opacity: 0.85; }
.fin-coverage--gap { color: var(--destructive); font-weight: 600; }
.fin-empty { color: var(--muted-foreground); padding: 16px 2px; font-size: 14px; }
.view-fin-a h3, .view-fin-b h3 { font-size: 18px; margin: 18px 0 10px; }
.fin-key { border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px;
  background: var(--card); margin-bottom: 8px; }
.fin-key__title { font-weight: 600; font-size: 14px; margin-bottom: 8px; }
.fin-key__item { display: flex; align-items: baseline; gap: 8px; padding: 2px 0; }
.fin-key__count { color: var(--muted-foreground); font-size: 12px; min-width: 32px; display: inline-block; }
.fin-key__desc { color: var(--muted-foreground); font-size: 13px; }
.fin-type { display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 11px; font-weight: 600;
  white-space: nowrap; background: var(--muted); color: var(--muted-foreground); }
.fin-type--appropriation { background: var(--diff-add); color: var(--diff-add-foreground); }
.fin-type--rescission, .fin-type--restriction { background: var(--diff-remove); color: var(--diff-remove-foreground); }
.fin-type--cap { background: var(--diff-modified); color: var(--diff-modified-foreground); }
.fin-type--transfer { background: var(--fin-transfer); color: var(--fin-transfer-foreground); }
.fin-type--authorization { background: var(--fin-authorization); color: var(--fin-authorization-foreground); }
.fin-type--fee { background: var(--fin-fee); color: var(--fin-fee-foreground); }
.fin-type--directive { background: var(--fin-directive); color: var(--fin-directive-foreground); }
.fin-type--earmark { background: var(--fin-earmark); color: var(--fin-earmark-foreground); }
.fin-type--availability { background: var(--fin-availability); color: var(--fin-availability-foreground); }
.fin-type--sub_allocation { background: var(--fin-sub-allocation); color: var(--fin-sub-allocation-foreground); }
.fin-sort { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; font-size: 13px; }
.fin-sort span { color: var(--muted-foreground); }
.fin-sort__btn { padding: 4px 12px; border: 1px solid var(--border); border-radius: 999px; background: var(--card);
  cursor: pointer; font: inherit; font-family: var(--font-sans); font-size: 13px; color: var(--foreground); }
.fin-sort__btn:hover { background: var(--secondary); }
.fin-sort__btn.is-active { background: var(--foreground); border-color: var(--foreground); color: var(--card); }
.fin-table { width: 100%; border-collapse: collapse; background: var(--card); border-radius: var(--radius);
  box-shadow: var(--shadow-soft); overflow: hidden; font-size: 14px; table-layout: fixed; }
.fin-table th { background: var(--accent); text-align: left; padding: 9px 12px; border-bottom: 2px solid var(--border);
  font-weight: 600; font-size: 13px; }
.fin-table td { padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
.fin-col-name { width: 72%; } .fin-col-type { width: 13%; } .fin-col-amount { width: 15%; text-align: right; }
.fin-amount { text-align: right; font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  white-space: nowrap; }
.fin-row, .fin-crow { cursor: pointer; }
.fin-row { background: var(--muted); font-weight: 600; }
.fin-row:hover td, .fin-crow:hover td { background: var(--secondary); }
.fin-row__name::before { content: "\\25b6"; font-size: 10px; margin-right: 8px; color: var(--muted-foreground);
  display: inline-block; transition: transform 0.15s; }
.fin-row.is-open .fin-row__name::before { transform: rotate(90deg); }
.fin-count { font-size: 11px; color: var(--muted-foreground); font-weight: 400; margin-left: 6px; }
.fin-review { background: var(--diff-modified); color: var(--diff-modified-foreground); font-size: 11px;
  padding: 1px 6px; border-radius: 999px; margin-left: 6px; font-weight: 500; white-space: nowrap; }
/* The warning mark (`_WARNING_ICON`): drawn in the surrounding text's colour and size. */
.fin-icon { display: inline-block; width: 1.15em; height: 1.15em; vertical-align: -0.2em; margin-right: 0.25em;
  fill: none; stroke: currentColor; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.fin-icon__dot { fill: currentColor; }
.fin-review[role="img"] .fin-icon { margin-right: 0; }
.fin-chip { font-size: 11px; font-weight: 600; border-radius: 4px; padding: 0 5px; margin-left: 6px; }
.fin-chip--law { background: var(--muted); color: var(--muted-foreground); border: 1px dashed var(--border); }
.fin-detail > td { padding: 0; background: var(--card); }
.fin-detail__inner { display: flex; }
.fin-detail__amounts { min-width: 220px; max-width: 260px; padding: 12px; border-right: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 10px; }
.fin-clause { display: flex; flex-direction: column; gap: 3px; align-items: flex-start; }
.fin-clause__amount { font-family: var(--font-mono); font-size: 15px; font-variant-numeric: tabular-nums; }
.fin-detail__text { padding: 12px 16px; font-size: 13px; line-height: 1.9; flex: 1; font-weight: 400; }
.fin-piece { border-radius: 3px; padding: 1px 3px; }
.fin-piece.fin-type--unknown { background: var(--muted); color: var(--foreground); }
.fin-proviso { color: var(--muted-foreground); }
.fin-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden;
  box-shadow: var(--shadow-soft); }
.fin-card .fin-table { box-shadow: none; border-radius: 0; table-layout: auto; }
.fin-summary { display: flex; gap: 18px; flex-wrap: wrap; padding: 10px 14px; border-bottom: 1px solid var(--border);
  font-size: 13px; color: var(--muted-foreground); }
.fin-summary b { color: var(--foreground); font-variant-numeric: tabular-nums; }
.fin-table--compare td { vertical-align: top; }
.fin-table--compare th { background: var(--secondary); font-size: 12px; text-transform: uppercase;
  letter-spacing: 0.04em; color: var(--muted-foreground); border-bottom: 1px solid var(--border); padding: 8px 10px; }
.fin-table--compare td { padding: 8px 10px; }
.fin-table--compare .fin-amount { font-family: var(--font-sans); }
.fin-stack__tag { white-space: nowrap; }
.fin-caret { color: var(--muted-foreground); width: 16px; display: inline-block; }
.fin-loc { font-weight: 600; }
.fin-loc small { display: block; font-weight: 400; color: var(--muted-foreground); font-size: 12px; }
/* Links from a comparison row to its sections in Version A / B. */
.fin-jumps { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 6px; }
.fin-jump { background: none; border: 0; padding: 0; font: inherit; font-size: 12px; color: var(--primary);
  text-decoration: underline; text-underline-offset: 2px; cursor: pointer; }
.fin-jump:hover { color: var(--foreground); }
.fin-jump--none { color: var(--muted-foreground); text-decoration: none; cursor: default; }
.fin-row.is-target > td { background: var(--accent); transition: background 0.6s ease; }
.fin-stack { display: grid; grid-template-columns: auto 1fr auto; gap: 2px 8px; align-items: center; }
.fin-ab { font-size: 11px; font-weight: 700; border-radius: 4px; padding: 0 5px; }
.fin-ab--a { background: var(--secondary); color: var(--primary); }
.fin-ab--b { background: var(--accent); color: var(--gold); }
.fin-stack__amt { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.fin-stack__amt--old { color: var(--muted-foreground); }
.fin-stack__amt--new { font-weight: 600; }
.fin-stack__none { color: var(--muted-foreground); font-style: italic; text-align: right; }
.fin-up { color: var(--diff-add-foreground); } .fin-down { color: var(--diff-remove-foreground); }
.fin-same { color: var(--muted-foreground); }
.fin-same small, .fin-up small, .fin-down small { display: block; font-size: 11px; }
.fin-cdetail > td { background: var(--background); padding: 12px 14px; }
.fin-subhead { font-size: 13px; font-weight: 600; color: var(--muted-foreground); padding: 14px 14px 6px;
  border-top: 1px solid var(--border); }
.fin-table--plain { box-shadow: none; }
@media (max-width: 820px) {
  .fin-table--compare { min-width: 640px; }
  .fin-detail__inner { flex-direction: column; }
  .fin-detail__amounts { max-width: none; border-right: 0; border-bottom: 1px solid var(--border); }
  .fin-col-name { width: 55%; } .fin-col-type { width: 20%; } .fin-col-amount { width: 25%; }
}

"""

FINANCIAL_JS = """\
document.addEventListener('DOMContentLoaded', function() {
  // Financial views (ADR 0023). Their text is read from the embedded document's full_text,
  // sliced by the ledger's offsets, so no bill text is duplicated into the page.
  var finDoc = null;
  function finData() {
    if (!finDoc) finDoc = JSON.parse(document.getElementById('diff-data').textContent);
    return finDoc;
  }
  // The prose between two full_text offsets as one line, read the way
  // `financial._prose_blocks` reads it: the PDF line-number gutter off, the unnumbered
  // running header skipped, lines joined with a space, and a word the printer broke
  // across lines (`speci-` / `fied`) rejoined.
  function finText(side, s, e) {
    var doc = finData(), ft = doc.full_text[side];
    var guttered = ((doc.versions || {})[side] || {}).source !== 'xml';
    var out = '', pos = ft.lastIndexOf('\\n', s - 1) + 1;
    while (pos < e) {
      var nl = ft.indexOf('\\n', pos);
      if (nl < 0) nl = ft.length;
      var cs = guttered ? pos + 7 : pos;
      var a = Math.max(cs, s), b = Math.min(nl, e);
      var furniture = guttered && !ft.slice(pos, Math.min(cs, nl)).trim()
        && /^\\s*[\\u2020\\u2021\\u2022]/.test(ft.slice(cs, nl));
      var body = b > a ? ft.slice(a, b).replace(/\\s+$/, '').replace(/^\\s+/, '') : '';
      if (body && !furniture) {
        if (out.length >= 2 && out.slice(-1) === '-' && /[A-Za-z0-9]/.test(out.charAt(out.length - 2))
            && /^[a-z]/.test(body)) {
          out = out.slice(0, -1) + body;
        } else {
          out = out ? out + ' ' + body : body;
        }
      }
      pos = nl + 1;
    }
    return out;
  }
  function finEsc(t) {
    return t.replace(/[&<>"]/g, function(c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; });
  }
  function finFillText(box) {
    if (box.dataset.done) return;
    var side = box.dataset.side, pieces = JSON.parse(box.dataset.pieces), html = '';
    pieces.forEach(function(p) {
      var t = finEsc(finText(side, p[0], p[1]));
      html += p[2] === 'proviso'
        ? '<strong class="fin-proviso">' + t + '</strong> '
        : '<span class="fin-piece fin-type--' + p[2] + '">' + t + '</span> ';
    });
    box.innerHTML = html;
    box.dataset.done = '1';
  }
  // Comparison: a row opens onto that change's card from the Changes view, cloned, so the
  // word diff shown is the Changes view's own.
  function finFillChange(detail) {
    var cell = detail.firstElementChild;
    if (cell.childElementCount) return;
    var card = document.getElementById(detail.dataset.change);
    if (!card) return;
    [].slice.call(card.children).forEach(function(part) {
      if (!part.classList.contains('change-header')) cell.appendChild(part.cloneNode(true));
    });
  }
  function finFill(detail) {
    if (detail.classList.contains('fin-cdetail')) finFillChange(detail);
    else finFillText(detail.querySelector('.fin-detail__text'));
  }
  // A detail row always follows its summary row (the sort moves them as a pair).
  function finToggle(detail) {
    var row = detail.previousElementSibling, open = detail.hidden;
    detail.hidden = !open;
    row.classList.toggle('is-open', open);
    row.setAttribute('aria-expanded', open ? 'true' : 'false');
    var caret = row.querySelector('.fin-caret');
    if (caret) caret.textContent = open ? '\\u25be' : '\\u25b8';
    if (open) finFill(detail);
  }
  document.querySelectorAll('.fin-table').forEach(function(table) {
    table.addEventListener('click', function(e) {
      var row = e.target.closest('tr.fin-row, tr.fin-crow');
      if (row) finToggle(row.nextElementSibling);
    });
    table.addEventListener('keydown', function(e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var row = e.target.closest('tr.fin-row, tr.fin-crow');
      if (row && row === e.target) { e.preventDefault(); finToggle(row.nextElementSibling); }
    });
  });
  // The page's search (`find:prepare` / `find:reveal` in the report script) reads the
  // collapsed rows too: their text is filled in before it indexes, and a hit opens its row.
  document.addEventListener('find:prepare', function(e) {
    if (!e.target.querySelectorAll) return;
    e.target.querySelectorAll('tr.fin-detail, tr.fin-cdetail').forEach(finFill);
  });
  document.addEventListener('find:reveal', function(e) {
    var detail = e.target;
    if (detail.matches && detail.matches('tr.fin-detail, tr.fin-cdetail') && detail.hidden) finToggle(detail);
  });
  // A comparison row's "Version A section" / "Version B section": switch to that view (through
  // its own tab, so everything a tab change does happens), open the section's row, show it.
  document.querySelectorAll('.fin-jump[data-group]').forEach(function(link) {
    link.addEventListener('click', function(e) {
      e.stopPropagation();  // the link sits in a comparison row, which opens on click
      var view = link.dataset.side === 'v1' ? 'fin-a' : 'fin-b';
      [].slice.call(document.querySelectorAll('.view-toggle__btn'))
        .filter(function(b) { return b.dataset.view === view; })
        .forEach(function(b) { b.click(); });
      var row = document.querySelector('.view-' + view + ' tr.fin-row[data-group="' + link.dataset.group + '"]');
      if (!row) return;
      if (row.nextElementSibling.hidden) finToggle(row.nextElementSibling);
      row.scrollIntoView({behavior: 'smooth', block: 'center'});
      row.classList.add('is-target');
      setTimeout(function() { row.classList.remove('is-target'); }, 1600);
      row.focus({preventScroll: true});
    });
  });
  // Sort: Default (bill order) / Amount, largest first / Needs review first. Each row
  // travels with its detail row.
  document.querySelectorAll('.fin-sort').forEach(function(bar) {
    var table = bar.parentElement.querySelector('.fin-table');
    if (!table || !table.tBodies.length) return;
    bar.addEventListener('click', function(e) {
      var btn = e.target.closest('.fin-sort__btn');
      if (!btn) return;
      var key = btn.dataset.sort, body = table.tBodies[0];
      var groups = [].slice.call(body.querySelectorAll('tr.fin-row')).map(function(r) {
        return [r, body.querySelector('tr.fin-detail[data-group="' + r.dataset.group + '"]')];
      });
      groups.sort(function(x, y) {
        if (key === 'amount') return parseFloat(y[0].dataset.amount) - parseFloat(x[0].dataset.amount);
        if (key === 'review') return (y[0].dataset.review - x[0].dataset.review)
          || (x[0].dataset.group - y[0].dataset.group);
        return x[0].dataset.group - y[0].dataset.group;
      });
      groups.forEach(function(g) { body.appendChild(g[0]); body.appendChild(g[1]); });
      bar.querySelectorAll('.fin-sort__btn').forEach(function(b) { b.classList.toggle('is-active', b === btn); });
    });
  });
  // Export Inferred Financials: that version's whole ledger, one row per dollar amount,
  // the breadcrumb spread into repeated columns so it sorts and pivots without nesting.
  var FIN_LEVEL_COLUMN = {division: 'division', title: 'title', major: 'department', agency: 'agency',
                          account: 'account', grouping: 'grouping', section: 'section', subsection: 'subsection'};
  function csvCell(v) {
    var t = v === null || v === undefined ? '' : String(v);
    return /[",\\r\\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
  }
  document.querySelectorAll('.fin-export').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var doc = finData(), side = btn.dataset.side, letter = side === 'v1' ? 'A' : 'B';
      var cols = ['version', 'version_label', 'location', 'division', 'title', 'department', 'agency',
                  'account', 'grouping', 'section', 'subsection', 'section_no', 'clause_no', 'clause_level',
                  'clause_type', 'amount', 'is_clause_amount', 'not_to_exceed', 'in_amended_law',
                  'needs_review', 'flags', 'clause_text'];
      var lines = [cols.join(',')];
      doc.financial[side].sections.forEach(function(sec, si) {
        var levels = {};
        sec.path.forEach(function(p) { var c = FIN_LEVEL_COLUMN[p[1]]; if (c) levels[c] = p[0]; });
        var location = sec.path.map(function(p) { return p[0]; }).join(' > ');
        sec.clauses.forEach(function(cl, ci) {
          var text = finText(side, cl.span[0], cl.span[1]);
          var clauseAmountTaken = false;
          cl.amounts.forEach(function(a) {
            var isClause = !clauseAmountTaken && a.value === cl.amount;
            if (isClause) clauseAmountTaken = true;
            lines.push([letter, doc.versions[side].label, location, levels.division, levels.title,
              levels.department, levels.agency, levels.account, levels.grouping, levels.section,
              levels.subsection, si + 1, ci + 1, cl.level, cl.type, a.value, isClause, a.cap,
              a.in_amended_law, cl.needs_review, sec.flags.join(' '), text].map(csvCell).join(','));
          });
        });
      });
      finDownload(lines, ['version', letter]);
    });
  });
  // The comparison's CSV: one row per comparison row, as the table shows it. The rows come
  // from Python (`comparison_export_rows`), so the difference is the table's own; the
  // change's words come from the embedded document.
  function finDownload(lines, suffix) {
    var bill = finData().bill || {};
    // A PDF upload knows its congress but not its bill number: leave out what is empty.
    var parts = [bill.congress, bill.type, bill.number].filter(function(x) { return x; });
    var name = ['financials'].concat(parts, suffix).join('-') + '.csv';
    var url = URL.createObjectURL(new Blob(['\\ufeff' + lines.join('\\r\\n')], {type: 'text/csv'}));
    var link = document.createElement('a');
    link.href = url; link.download = name;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
  }
  var compareBtn = document.querySelector('.fin-export-compare');
  if (compareBtn) compareBtn.addEventListener('click', function() {
    var doc = finData(), rows = JSON.parse(document.getElementById('fin-compare-data').textContent);
    var cols = ['change_index', 'change_type', 'location', 'version_a_label', 'version_b_label',
                'a_sections', 'a_types', 'a_amounts', 'a_given_out', 'b_sections', 'b_types', 'b_amounts',
                'b_given_out', 'difference', 'difference_note', 'a_needs_review', 'b_needs_review', 'flags',
                'a_amended_law_amounts', 'b_amended_law_amounts', 'text_before', 'text_after'];
    var lines = [cols.join(',')];
    function joined(list) { return list.join(' | '); }
    // One line per change: before and after side by side, each as one line of words.
    function oneLine(t) { return t ? String(t).replace(/\\s+/g, ' ').trim() : ''; }
    rows.forEach(function(r) {
      var text = (doc.changes[r.change_index] || {}).text || {};
      var flags = r.a.flags.concat(r.b.flags.filter(function(f) { return r.a.flags.indexOf(f) < 0; }));
      lines.push([r.change_index, r.change_type, r.location, doc.versions.v1.label, doc.versions.v2.label,
        joined(r.a.sections), joined(r.a.types), joined(r.a.amounts), r.a.given_out,
        joined(r.b.sections), joined(r.b.types), joined(r.b.amounts), r.b.given_out,
        r.difference, r.difference_note, r.a.needs_review, r.b.needs_review, flags.join(' '),
        joined(r.a.amended_law_amounts), joined(r.b.amended_law_amounts), oneLine(text.old), oneLine(text.new)]
        .map(csvCell).join(','));
    });
    finDownload(lines, ['comparison']);
  });
});
"""
