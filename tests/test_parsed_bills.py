"""What the shared parse in `tests/parsed_bills.py` must not do.

The corpus gates read their trees through `parsed_bill`, which returns one shared
`BillTree` per file per process. Both tests protect the same thing: a gate asserts
against the tree the parser produces from the file's current bytes, never against
one an earlier test changed or one parsed from bytes the file no longer has.
"""

from __future__ import annotations

import shutil

import pytest

from deltatrack.bill_tree import normalize_bill
from tests.corpus_paths import PROJECT_ROOT
from tests.parsed_bills import parsed_bill

_SMALL_BILL = PROJECT_ROOT / "tests" / "fixtures" / "resolutions" / "BILLS-119hjres25ih.xml"


@pytest.fixture
def bill(tmp_path):
    copy = tmp_path / _SMALL_BILL.name
    shutil.copyfile(_SMALL_BILL, copy)
    return copy


def test_a_changed_shared_tree_is_refused_rather_than_served(bill):
    """A test that pops from the shared node list would otherwise hand every later
    gate in the process a tree the parser never produced, and the gate would pass or
    fail on that. Red if `parsed_bill` drops its snapshot comparison."""
    parsed_bill(bill).nodes.pop()

    with pytest.raises(AssertionError, match="changed the node list"):
        parsed_bill(bill)


def test_a_rewritten_file_is_parsed_again(bill):
    """Unchanged bytes share one parse, which is the point of the module. Rewritten
    bytes must not: serving the earlier tree would certify text the file no longer
    holds. Red if the key drops the file's mtime and size."""
    first = parsed_bill(bill)
    assert parsed_bill(bill) is first

    bill.write_text(bill.read_text().replace("That Congress disapproves", "That Congress approves"))

    assert parsed_bill(bill) == normalize_bill(bill) != first
