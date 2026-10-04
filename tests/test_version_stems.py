"""Pin the version-prefixed filename-stem parsers in version_stems."""

from __future__ import annotations

from pathlib import Path

import pytest

from deltatrack.version_stems import (
    VersionIdentity,
    label_from_stem,
    local_versions,
    resolve_version_file,
    version_identity_from_filename,
    version_number_from_stem,
)


@pytest.fixture
def bills_dir(tmp_path: Path) -> Path:
    """A synthetic download root, built under a temp dir rather than read from disk.

    Never the real download tree: these are resolver tests, so the input has to be
    constructed to contain the shapes being pinned (double-digit ordinals, labels made
    of digits and underscores) rather than whatever happens to be fetched locally.
    """
    root = tmp_path / "bills"
    root.mkdir()
    return root


def _make_bill(bills_dir: Path, slug: str, *filenames: str) -> Path:
    """A bill folder holding `filenames`, each with placeholder bytes."""
    bill_dir = bills_dir / slug
    bill_dir.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        (bill_dir / name).write_text("<bill/>")
    return bill_dir


class TestVersionNumberFromStem:
    def test_numeric_prefix(self):
        assert version_number_from_stem("1_reported-in-house") == 1

    def test_multi_digit_prefix(self):
        assert version_number_from_stem("10_engrossed") == 10

    def test_no_numeric_prefix(self):
        assert version_number_from_stem("draft-v2") is None

    def test_empty_stem(self):
        assert version_number_from_stem("") is None

    def test_a_digit_int_cannot_parse_answers_none_rather_than_raising(self):
        """`"³".isdigit()` is True but `int("³")` raises, so the test must be isdecimal.

        Reachable since #152: `local_versions` reads whatever filenames are on disk, so
        an `isdigit` guard turned an oddly named file into a ValueError traceback.
        """
        assert version_number_from_stem("³_reported-in-house") is None
        assert version_number_from_stem("½_draft") is None


class TestLabelFromStem:
    def test_strips_numeric_prefix(self):
        assert label_from_stem("1_reported-in-house") == "reported-in-house"

    def test_no_prefix_returned_unchanged(self):
        assert label_from_stem("draft-v2") == "draft-v2"

    def test_non_numeric_prefix_not_stripped(self):
        # Numeric-only strip: "foo_" is not a version prefix, so the stem stands.
        assert label_from_stem("foo_bar") == "foo_bar"

    def test_empty_stem(self):
        assert label_from_stem("") == ""

    def test_a_prefix_that_is_not_an_ordinal_is_not_stripped(self):
        """Mirrors version_number_from_stem: what cannot become an ordinal is not one."""
        assert label_from_stem("³_reported-in-house") == "³_reported-in-house"


class TestVersionIdentityFromFilename:
    """The one filename-to-version rule every surface calls (#692).

    The stem rules are pinned above; these pin what a filename adds on top: the
    extension, path components an uploader can send, and the fallback.
    """

    def test_a_numbered_corpus_name_gives_label_and_ordinal(self):
        assert version_identity_from_filename("1_reported-in-house.pdf", fallback="Start") == VersionIdentity(
            "reported-in-house", 1
        )

    @pytest.mark.parametrize(
        "name",
        [
            "../../etc/2_engrossed-in-house.XML",
            "C:\\Users\\a\\2_engrossed-in-house.xml",
            "/abs/2_engrossed-in-house.pdf",
        ],
    )
    def test_path_components_are_dropped_before_anything_is_read(self, name):
        """An upload's filename is client-supplied; only its last component names a version."""
        assert version_identity_from_filename(name, fallback="End") == VersionIdentity("engrossed-in-house", 2)

    @pytest.mark.parametrize("name", ["BILLS-118hr4366enr.xml", "enr_enrolled-bill.pdf", "chairs-mark.pdf"])
    def test_a_name_without_an_ordinal_prefix_keeps_its_label_and_no_ordinal(self, name):
        """A GPO stage code (`enr`, `ih`) is a legislative stage, not this bill's local
        ordinal, and an arbitrary draft name has none: both stay unknown, label intact."""
        identity = version_identity_from_filename(name, fallback="Start")
        assert identity == VersionIdentity(name.rsplit(".", 1)[0], None)

    @pytest.mark.parametrize("name", [None, "", "   ", ".pdf", "dir/"])
    def test_a_name_that_yields_nothing_falls_back(self, name):
        assert version_identity_from_filename(name, fallback="Start version") == VersionIdentity("Start version", None)


class TestLocalVersions:
    def test_lists_number_and_label_ascending(self, bills_dir):
        """The bare-slug listing's data: every version, ordinal-ordered (#152)."""
        _make_bill(
            bills_dir,
            "118-hr-4366",
            "6_enrolled-bill.xml",
            "1_reported-in-house.xml",
            "3_placed-on-calendar-senate.xml",
        )
        assert local_versions(bills_dir, "118-hr-4366") == [
            (1, "reported-in-house"),
            (3, "placed-on-calendar-senate"),
            (6, "enrolled-bill"),
        ]

    def test_ordinals_sort_numerically_not_lexicographically(self, bills_dir):
        """`10` comes after `2`. A stem sort would put it after `1` and before `2`."""
        _make_bill(bills_dir, "118-hr-4366", "1_introduced.xml", "2_engrossed.xml", "10_enrolled.xml")
        assert [n for n, _ in local_versions(bills_dir, "118-hr-4366")] == [1, 2, 10]

    def test_unnumbered_stems_are_left_out(self, bills_dir):
        """A file with no `<n>_` prefix cannot be picked by ordinal, so it is not offered."""
        _make_bill(bills_dir, "118-hr-4366", "1_reported-in-house.xml", "scratch-copy.xml")
        assert local_versions(bills_dir, "118-hr-4366") == [(1, "reported-in-house")]

    def test_other_extensions_are_ignored(self, bills_dir):
        """XML compare only (#152): a PDF sibling is not an XML version."""
        _make_bill(bills_dir, "118-hr-4366", "1_reported-in-house.xml", "2_engrossed-in-house.pdf")
        assert local_versions(bills_dir, "118-hr-4366") == [(1, "reported-in-house")]

    def test_missing_bill_folder_lists_nothing(self, bills_dir):
        assert local_versions(bills_dir, "119-hr-1") == []

    def test_a_numeric_looking_prefix_int_rejects_does_not_raise(self, bills_dir):
        """A file named `³_x.xml` is listed as unnumbered, not as a ValueError."""
        _make_bill(bills_dir, "118-hr-4366", "1_reported-in-house.xml", "³_odd.xml")
        assert local_versions(bills_dir, "118-hr-4366") == [(1, "reported-in-house")]


class TestResolveVersionFile:
    def test_resolves_slug_and_ordinal_to_its_file(self, bills_dir):
        bill_dir = _make_bill(bills_dir, "118-hr-4366", "1_reported-in-house.xml", "6_enrolled-bill.xml")
        assert resolve_version_file(bills_dir, "118-hr-4366", 6) == bill_dir / "6_enrolled-bill.xml"

    def test_a_label_of_digits_and_underscores_does_not_confuse_the_match(self, bills_dir):
        """The ordinal is the `<n>_` prefix, never anything the label happens to contain."""
        bill_dir = _make_bill(bills_dir, "118-hr-4366", "1_engrossed_amendment_2.xml", "2_ref_to_1_committee.xml")
        assert resolve_version_file(bills_dir, "118-hr-4366", 1) == bill_dir / "1_engrossed_amendment_2.xml"
        assert resolve_version_file(bills_dir, "118-hr-4366", 2) == bill_dir / "2_ref_to_1_committee.xml"

    def test_an_ordinal_is_not_a_prefix_of_a_longer_one(self, bills_dir):
        """`3` must not reach `30_...`, which a bare `startswith` on the digits would."""
        bill_dir = _make_bill(bills_dir, "118-hr-4366", "3_placed-on-calendar-senate.xml", "30_enrolled.xml")
        assert resolve_version_file(bills_dir, "118-hr-4366", 3) == bill_dir / "3_placed-on-calendar-senate.xml"
        assert resolve_version_file(bills_dir, "118-hr-4366", 30) == bill_dir / "30_enrolled.xml"

    def test_out_of_range_ordinal_resolves_to_nothing(self, bills_dir):
        _make_bill(bills_dir, "118-hr-4366", "1_reported-in-house.xml")
        assert resolve_version_file(bills_dir, "118-hr-4366", 9) is None

    def test_missing_bill_folder_resolves_to_nothing(self, bills_dir):
        assert resolve_version_file(bills_dir, "119-hr-1", 1) is None

    def test_the_extension_is_part_of_the_address(self, bills_dir):
        """A PDF-only version is not an XML version, so the XML resolver misses it."""
        _make_bill(bills_dir, "118-hr-4366", "2_engrossed-in-house.pdf")
        assert resolve_version_file(bills_dir, "118-hr-4366", 2) is None
