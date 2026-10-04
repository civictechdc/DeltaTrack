"""Integration tests for the XML diff dict -> HTML rendering path.

Exercises the full dict -> view-model -> HTML pipeline plus CLI integration.
Internal builders are tested directly via the test_formatters_diff_html_*.py
modules; word_diff lives in formatters._text.
"""

import pytest

from deltatrack.formatters._text import word_diff
from deltatrack.formatters.canonical import xml_diff_to_canonical
from deltatrack.formatters.diff_html import format_diff_html


def format_html(diff_dict):
    """Local helper preserving the historical dict -> HTML entry point."""
    return format_diff_html(xml_diff_to_canonical(diff_dict))


class TestWordDiff:
    def test_identical_text_no_markup(self):
        result = word_diff("hello world", "hello world")
        assert "<ins>" not in result
        assert "<del>" not in result
        assert "hello world" == result

    def test_single_word_changed(self):
        result = word_diff("the cat sat", "the dog sat")
        assert "<del>cat</del>" in result
        assert "<ins>dog</ins>" in result
        assert "the" in result
        assert "sat" in result

    def test_dollar_amount_change(self):
        result = word_diff(
            "appropriated $1,000,000 for construction",
            "appropriated $2,500,000 for construction",
        )
        assert "<del>$1,000,000</del>" in result
        assert "<ins>$2,500,000</ins>" in result
        assert "appropriated" in result
        assert "for construction" in result

    def test_low_similarity_returns_none(self):
        result = word_diff(
            "this is completely different text about one topic",
            "nothing here matches the original at all whatsoever",
        )
        assert result is None

    def test_html_characters_escaped(self):
        result = word_diff(
            "amount < $1,000 & more",
            "amount < $2,000 & more",
        )
        assert "&lt;" in result
        assert "&amp;" in result
        # Should not contain raw < or & (outside of tags)
        # The <del> and <ins> tags are expected
        stripped = result.replace("<del>", "").replace("</del>", "")
        stripped = stripped.replace("<ins>", "").replace("</ins>", "")
        assert "<" not in stripped
        assert "&" in stripped  # escaped entities contain &

    def test_addition_at_end(self):
        result = word_diff("first second", "first second third")
        assert "<ins>third</ins>" in result

    def test_deletion_at_end(self):
        result = word_diff("first second third", "first second")
        assert "<del>third</del>" in result

    def test_empty_strings(self):
        result = word_diff("", "")
        assert result == ""

    def test_threshold_boundary(self):
        # Two texts that are somewhat similar but below default threshold
        # should return None
        result = word_diff("a b c d e", "v w x y z")
        assert result is None

    def test_custom_threshold(self):
        # With threshold=0.0, even very different texts produce a diff
        result = word_diff("a b c", "x y z", threshold=0.0)
        assert result is not None


def _sample_diff_dict(**overrides):
    """Build a minimal diff dict for testing format_html."""
    base = {
        "old_version": "reported-in-house",
        "new_version": "engrossed-in-house",
        "congress": 118,
        "bill_type": "hr",
        "bill_number": 4366,
        "summary": {"added": 1, "removed": 0, "modified": 1, "unchanged": 5, "moved": 0},
        "changes": [
            {
                "display_path_old": ["DEPT", "Army"],
                "display_path_new": ["DEPT", "Army"],
                "match_path": ["dept", "army"],
                "change_type": "modified",
                "old_text": "appropriated $1,000,000 for construction",
                "new_text": "appropriated $2,000,000 for construction",
                "text_diff": [],
                "section_number": "Sec. 101",
                "element_id_old": "old-1",
                "element_id_new": "new-1",
                "financial": {
                    "old_amounts": [1000000],
                    "new_amounts": [2000000],
                    "amounts_changed": True,
                    "paired_amounts": [[1000000, 2000000]],
                },
            },
            {
                "display_path_old": None,
                "display_path_new": ["DEPT", "Navy"],
                "match_path": ["dept", "navy"],
                "change_type": "added",
                "old_text": None,
                "new_text": "new navy section text",
                "text_diff": [],
                "section_number": "",
                "element_id_old": None,
                "element_id_new": "new-2",
            },
        ],
    }
    base.update(overrides)
    return base


class TestFormatHtml:
    def test_valid_html_structure(self):
        html = format_html(_sample_diff_dict())
        assert html.startswith("<!DOCTYPE html>")
        assert "<html" in html
        assert "<head>" in html
        assert "<body>" in html
        assert "</html>" in html

    def test_header_shows_bill_info(self):
        html = format_html(_sample_diff_dict())
        assert "HR 4366" in html or "hr 4366" in html.lower()
        assert "118" in html
        assert "reported-in-house" in html
        assert "engrossed-in-house" in html

    def test_header_shows_version_numbers(self):
        diff = _sample_diff_dict()
        diff["old_version_number"] = 1
        diff["new_version_number"] = 2
        html = format_html(diff)
        # Should show version numbers alongside names
        assert "v1" in html.lower() or "version 1" in html.lower()
        assert "v2" in html.lower() or "version 2" in html.lower()

    def test_header_without_version_numbers(self):
        """Version numbers are optional, header still works without them."""
        diff = _sample_diff_dict()
        # No version_number keys
        html = format_html(diff)
        assert "reported-in-house" in html

    def test_summary_counts(self):
        html = format_html(_sample_diff_dict())
        assert "added" in html
        assert "modified" in html

    def test_renders_no_financial_presentation(self):
        """#671 — the report makes no paired-amount claim, even when the diff carries one.

        The sample diff still carries `financial.paired_amounts` on purpose. No producer
        emits it any more -- #671 dropped it from the `--financial` JSON and #687 removed
        the dataclass field that computed it -- and that is exactly why it stays here as a
        hand-written hostile input: it covers the whole chain from a diff dict carrying a
        paired claim to rendered HTML, so re-adding EITHER the canonical field or the
        renderer that read it turns this red. It cannot pass vacuously for want of data.

        Until amounts can be typed to accounts (#115, #175), the report presents no
        dollar figure as a change.
        """
        html = format_html(_sample_diff_dict())
        assert "Financial Summary" not in html
        assert "financial-table" not in html
        assert "financial-callout" not in html
        assert "data-financial" not in html
        # The prose bodies still show the bill's own text, amounts included.
        assert "$1,000,000" in html
        assert "$2,000,000" in html

    def test_contains_sidebar(self):
        html = format_html(_sample_diff_dict())
        assert "sidebar" in html
        assert 'name="change-filter"' in html

    def test_contains_change_cards(self):
        html = format_html(_sample_diff_dict())
        assert 'id="change-0"' in html
        assert 'id="change-1"' in html

    def test_contains_inline_css(self):
        html = format_html(_sample_diff_dict())
        assert "<style>" in html

    def test_contains_inline_js(self):
        html = format_html(_sample_diff_dict())
        assert "<script>" in html

    def test_empty_changes(self):
        diff = _sample_diff_dict(
            changes=[],
            summary={
                "added": 0,
                "removed": 0,
                "modified": 0,
                "unchanged": 0,
                "moved": 0,
            },
        )
        html = format_html(diff)
        assert "<!DOCTYPE html>" in html
        assert "No changes found" in html


class TestCliIntegration:
    def test_format_flag_accepted(self):
        from deltatrack.diff_bill import build_parser

        parser = build_parser()
        args = parser.parse_args(["compare", "a.xml", "b.xml", "--format", "html"])
        assert args.format == "html"

    def test_format_default_is_html(self):
        from deltatrack.diff_bill import build_parser

        parser = build_parser()
        args = parser.parse_args(["compare", "a.xml", "b.xml"])
        assert args.format == "html"

    @pytest.mark.slow
    def test_format_html_output(self, tmp_path, monkeypatch, fast_normalize_diff):
        """HTML format produces a valid HTML file via the CLI."""
        import sys

        from conftest import HR4366_V1_PATH, HR4366_V2_PATH

        from deltatrack.diff_bill import main

        out = tmp_path / "report.html"
        monkeypatch.setattr(
            sys,
            "argv",
            ["diff_bill.py", "compare", str(HR4366_V1_PATH), str(HR4366_V2_PATH), "--format", "html", "-o", str(out)],
        )
        main()
        assert out.read_text().startswith("<!DOCTYPE html>")

    @pytest.mark.slow
    def test_format_html_real_bill_renders_no_financial_presentation(self, tmp_path, monkeypatch, fast_normalize_diff):
        """#671 — end to end on a real bill with genuine amount changes.

        v1 -> v6 (enrolled) is the comparison that DID render a Financial Summary,
        so it is the case with something to lose: this is the same absence check as
        the unit test above, run through the CLI on real appropriations text rather
        than a hand-built diff dict.
        """
        import sys

        from conftest import HR4366_V1_PATH, HR4366_V6_PATH

        from deltatrack.diff_bill import main

        out = tmp_path / "report.html"
        monkeypatch.setattr(
            sys,
            "argv",
            ["diff_bill.py", "compare", str(HR4366_V1_PATH), str(HR4366_V6_PATH), "--format", "html", "-o", str(out)],
        )
        main()
        html = out.read_text()
        assert "Financial Summary" not in html
        assert "financial-table" not in html
        assert "financial-callout" not in html
        assert "data-financial" not in html
