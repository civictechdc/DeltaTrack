from __future__ import annotations

import pytest

from shared.bill_types import BILL_TYPES, resolve_bill_types


def test_empty_defaults_to_all() -> None:
    assert resolve_bill_types([]) == list(BILL_TYPES)


def test_list_containing_all_resolves_to_all_bill_types() -> None:
    assert resolve_bill_types(["hr", "ALL"]) == list(BILL_TYPES)


def test_bill_types_are_case_insensitive() -> None:
    assert resolve_bill_types(["HR", "sJrEs"]) == ["hr", "sjres"]


def test_invalid_bill_type_raises_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        resolve_bill_types(["hr", "not-a-type"])

    # Example error message:
    # Unknown bill type 'not-a-type'. Bill type must be one of: 'hr', 's', 'hjres', 'sjres', ...
    message = str(excinfo.value)
    for part in ["Unknown bill type", "not-a-type", *BILL_TYPES.keys(), "all"]:
        assert part in message, f"Missing {part!r} in message: {message}"
