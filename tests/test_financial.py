"""The ledger's rules, one per test, on synthetic text (ADR 0023).

`deltatrack.financial` reads each money-bearing section of a version's structure tree,
splits it into clauses, types each clause and keeps every amount. These tests pin the
reading of the text (what a PDF block looks like once its print furniture is gone) and the
one safety rule the views depend on: an amount inside an amendment to another law is never
money given out. The corpus-level parity with the research notebook is in
`tests/test_financial_corpus.py`.
"""

from __future__ import annotations

from deltatrack.financial import (
    _amended_law_ranges,
    _in_amended_law,
    _prose_blocks,
    classify_text,
    financial_for,
    given_out,
    section_ledger,
)
from deltatrack.formatters.financial_views import comparison_rows


def pdf_text(*lines: tuple[int | None, str]) -> str:
    """PDF canonical full_text: each line behind a 7-char line-number gutter."""
    return "\n".join((f"{n:>5}" if n is not None else " " * 5) + "  " + text for n, text in lines)


def blocks(text: str, *, guttered: bool = True) -> list[str]:
    return [b for b, _offsets in _prose_blocks(text, 0, len(text), guttered=guttered)]


def node(text: str, label: str = "OPERATIONS AND SUPPORT", level: str = "account") -> list[dict]:
    return [{"label": label, "level": level, "full_text_span": {"start": 0, "end": len(text)}, "children": []}]


class TestReadingThePdfBlock:
    def test_the_gutter_and_the_opening_headings_come_off(self):
        text = pdf_text(
            (10, "MILITARY CONSTRUCTION, ARMY"),
            (11, "(INCLUDING TRANSFER OF FUNDS)"),
            (12, "For acquisition, construction, installation,"),
            (13, "$1,517,455,000, to remain available."),
        )
        assert blocks(text) == ["For acquisition, construction, installation, $1,517,455,000, to remain available."]

    def test_every_character_maps_back_to_the_embedded_text(self):
        # The views slice the embedded text by these offsets instead of carrying a copy.
        text = pdf_text((1, "HEADING"), (2, "For necessary"), (3, "expenses, $5,000."))
        [(clean, offsets)] = _prose_blocks(text, 0, len(text), guttered=True)
        assert len(offsets) == len(clean)
        assert [text[o] for o, ch in zip(offsets, clean) if ch != " "] == [ch for ch in clean if ch != " "]
        assert offsets == sorted(offsets)

    def test_the_running_header_is_skipped(self):
        # `† HR 4366 EAS`, unnumbered, sits between two lines of one sentence.
        text = pdf_text(
            (23, "SEC. 111. None of the funds may be obligated unless"),
            (None, "† HR 4366 EAS"),
            (1, "such contracts are awarded to United States firms, $500,000."),
        )
        assert blocks(text) == [
            "SEC. 111. None of the funds may be obligated unless such contracts are awarded to"
            " United States firms, $500,000."
        ]

    def test_a_word_broken_across_lines_is_rejoined(self):
        # Within a page `pdf_text` already rejoins; this is the break at a page seam.
        text = pdf_text((22, "amounts, speci-"), (1, "fied in the table, $52,683,000."))
        assert blocks(text) == ["amounts, specified in the table, $52,683,000."]

    def test_a_real_compound_keeps_its_hyphen(self):
        text = pdf_text((1, "the Child-"), (2, "Rescue program, $5,000."))
        assert blocks(text) == ["the Child- Rescue program, $5,000."]

    def test_a_heading_after_a_sentence_starts_a_new_paragraph(self):
        # FEDERAL-AID HIGHWAYS prints its limitation, then its payment under a qualifier.
        text = pdf_text(
            (9, "Funds shall not exceed $60,095,782,888 for fiscal year 2024."),
            (17, "(LIQUIDATION OF CONTRACT AUTHORIZATION)"),
            (19, "For the payment of obligations, $60,792,659,888 shall be derived."),
        )
        assert blocks(text) == [
            "Funds shall not exceed $60,095,782,888 for fiscal year 2024.",
            "For the payment of obligations, $60,792,659,888 shall be derived.",
        ]

    def test_an_all_caps_line_mid_sentence_is_a_continuation(self):
        text = pdf_text(
            (1, "(7) $68,438.40 from funds available in the account"),
            (2, "(69 X 0641);"),
            (3, "(8) $133,231.12 from funds available."),
        )
        assert blocks(text) == [
            "(7) $68,438.40 from funds available in the account (69 X 0641); (8) $133,231.12 from funds available."
        ]

    def test_a_subsection_after_a_sentence_starts_a_new_paragraph(self):
        text = pdf_text(
            (1, "SEC. 5. (a) None of the funds, $500,000, may be used."),
            (2, "(b) None of the funds, $500,000, may be used."),
        )
        assert blocks(text) == [
            "SEC. 5. (a) None of the funds, $500,000, may be used.",
            "(b) None of the funds, $500,000, may be used.",
        ]

    def test_a_subsection_inside_a_quotation_does_not(self):
        text = pdf_text(
            (1, "is amended to read: ‘‘(a) The Secretary shall pay $5."), (2, "(b) The Secretary shall pay $6.’’.")
        )
        assert len(blocks(text)) == 1

    def test_xml_text_is_joined_and_not_split(self):
        # The XML reader gives each subsection its own node already.
        text = "For expenses, $5,000:\n    (b) and $6,000."
        assert blocks(text, guttered=False) == ["For expenses, $5,000: (b) and $6,000."]


class TestClassifying:
    def test_quote_marks_do_not_push_a_clause_past_a_rule(self):
        # The XML the rules were built on carries no quote marks here; the PDF prints GPO's.
        lead = "Of the amounts appropriated to the Department for "
        names = ", ".join(f"''Account {i}''" for i in range(20))
        text = pdf_text((1, lead + names + ", up to $430,532,000 may be transferred to the Fund."))
        [section] = section_ledger(text, node(text, "SEC. 219", "section"), guttered=True)
        assert section["clauses"][0]["type"] == "transfer"
        assert classify_text(lead + names.replace("''", "") + ", up to $430,532,000 may be transferred") == "transfer"

    def test_the_section_enumerator_is_not_read(self):
        text = pdf_text((1, "SEC. 101. None of the funds made available, $5,000, may be used."))
        [section] = section_ledger(text, node(text, "SEC. 101", "section"), guttered=True)
        assert section["clauses"][0]["type"] == "restriction"

    def test_a_ceiling_is_not_the_clause_amount(self):
        text = pdf_text((1, "For expenses, not to exceed $2,000, $9,000."))
        [section] = section_ledger(text, node(text), guttered=True)
        clause = section["clauses"][0]
        assert clause["amount"] == 9000 and [a["cap"] for a in clause["amounts"]] == [True, False]


class TestAmendedLaw:
    def test_a_quoted_amount_is_in_amended_law(self):
        text = "Section 5 is amended by striking ‘‘$1,500’’ and inserting ‘‘$2,000’’."
        ranges = _amended_law_ranges(text)
        assert all(_in_amended_law(text, text.index(v), ranges) for v in ("$1,500", "$2,000"))

    def test_text_after_an_amendment_lead_in_is_in_amended_law(self):
        # The XML serializer drops the quote marks; the lead-in stands in for them.
        text = "Section 3(u) is amended to read as follows: (u) the plan costs $12,000."
        assert _in_amended_law(text, text.index("$12,000"), _amended_law_ranges(text))

    def test_an_amount_the_bill_gives_out_is_not(self):
        text = "For necessary expenses, $5,000, to remain available."
        assert not _in_amended_law(text, text.index("$5,000"), _amended_law_ranges(text))

    def test_an_amended_law_amount_is_never_money_given_out(self):
        assert given_out({"type": "appropriation", "amount": 5, "in_amended_law": True}) is None
        assert given_out({"type": "appropriation", "amount": 5, "in_amended_law": False}) == 5
        assert given_out({"type": "rescission", "amount": 5, "in_amended_law": False}) == -5
        assert given_out({"type": "cap", "amount": 5, "in_amended_law": False}) is None


class TestFlags:
    def test_a_further_section_inside_the_prose_is_flagged(self):
        # The parser did not start `SEC. 119A.`, so its money rides in SEC. 119's row.
        text = pdf_text(
            (1, "SEC. 119. None of the funds, $5, may be used."), (2, "SEC. 119A. None of the funds, $6, may be used.")
        )
        [section] = section_ledger(text, node(text, "SEC. 119", "section"), guttered=True)
        assert section["flags"] == ["may_hold_several_sections"]

    def test_the_section_opening_its_own_block_is_not(self):
        text = pdf_text((1, "SEC. 119. None of the funds, $5, may be used."))
        [section] = section_ledger(text, node(text, "SEC. 119", "section"), guttered=True)
        assert section["flags"] == []


class TestComparison:
    def _canonical(self, a_text: str, b_text: str) -> dict:
        full_text = {"v1": a_text, "v2": b_text}
        tree = {"v1": node(a_text, "SEC. 5", "section"), "v2": node(b_text, "SEC. 5", "section")}
        return {
            "full_text": full_text,
            "financial": financial_for(full_text, tree, "xml"),
            "changes": [
                {
                    "change_type": "modified",
                    "path": {"v1": ["SEC. 5"], "v2": ["SEC. 5"]},
                    "full_text_span": {"v1": {"start": 0, "end": len(a_text)}, "v2": {"start": 0, "end": len(b_text)}},
                }
            ],
        }

    def test_a_row_is_the_change_whose_section_holds_money(self):
        canonical = self._canonical("For expenses, $5,000.", "For expenses, $7,000.")
        [row] = comparison_rows(canonical)
        assert row["change_index"] == 0 and len(row["a"]) == len(row["b"]) == 1

    def test_an_amended_law_amount_never_enters_the_difference(self):
        # Both versions quote a figure in the law they amend; only the quoted figure moves.
        from deltatrack.formatters.financial_views import _difference_html

        canonical = self._canonical(
            "Section 5 of the Act is amended to read as follows: (a) The limit is $1,500.",
            "Section 5 of the Act is amended to read as follows: (a) The limit is $2,000.",
        )
        [row] = comparison_rows(canonical)
        html, delta = _difference_html(row)
        assert delta == 0 and "not given-out money" in html

    def test_the_comparison_export_is_the_tables_own_rows(self):
        from deltatrack.formatters.financial_views import _difference_html, comparison_export_rows

        canonical = self._canonical("For expenses, $5,000.", "For expenses, $7,000.")
        [row] = comparison_rows(canonical)
        [entry] = comparison_export_rows(canonical)
        assert entry["difference"] == _difference_html(row)[1] == 2000
        assert (entry["a"]["amounts"], entry["b"]["amounts"]) == ([5000], [7000])
        assert entry["change_index"] == row["change_index"] and entry["location"] == "SEC. 5"

    def test_the_comparison_export_says_why_there_is_no_difference(self):
        from deltatrack.formatters.financial_views import comparison_export_rows

        canonical = self._canonical(
            "Section 5 of the Act is amended to read as follows: (a) The limit is $1,500.",
            "Section 5 of the Act is amended to read as follows: (a) The limit is $2,000.",
        )
        [entry] = comparison_export_rows(canonical)
        assert entry["difference"] is None and entry["difference_note"] == "not given-out money"
        assert (entry["a"]["amended_law_amounts"], entry["b"]["amended_law_amounts"]) == ([1500], [2000])
