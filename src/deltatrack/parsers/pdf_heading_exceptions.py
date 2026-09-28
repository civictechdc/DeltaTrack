"""Named exceptions: the only wording that decides PDF heading structure (ADR 0022).

ADR 0018 keeps appropriations wording out of structure: structure comes from format. ADR 0022
admits a short list of named exceptions on top of that, each tied to one definable edge case and
kept only while a backtest shows it fixes headings without misfiling any amount, judged against
the raw XML file's heading tags. They are consulted by ``pdf_heading_passes`` when it decides
whether two stacked heading lines are one heading; everything else there is format.

This module is on the ``tests/test_structure_vocabulary_gate.py`` allowlist for that reason, and
only for it: a new wording rule belongs here, with its own measurement, or nowhere.
"""

from __future__ import annotations

import re

# A line that is exactly one of these is its own sub-account, never the tail of the line above
# (it sits under an agency: TREASURY INSPECTOR GENERAL FOR TAX ADMINISTRATION / SALARIES AND EXPENSES).
GENERIC_SUBACCOUNTS = frozenset({"SALARIES AND EXPENSES"})

# The 15 executive departments (5 U.S.C. 101). A line that is exactly one of these is an umbrella
# heading, never the first line of a longer name (DEPARTMENT OF DEFENSE / MILITARY UNACCOMPANIED
# HOUSING IMPROVEMENT FUND).
DEPARTMENTS = frozenset(
    {
        "DEPARTMENT OF AGRICULTURE",
        "DEPARTMENT OF COMMERCE",
        "DEPARTMENT OF DEFENSE",
        "DEPARTMENT OF EDUCATION",
        "DEPARTMENT OF ENERGY",
        "DEPARTMENT OF HEALTH AND HUMAN SERVICES",
        "DEPARTMENT OF HOMELAND SECURITY",
        "DEPARTMENT OF HOUSING AND URBAN DEVELOPMENT",
        "DEPARTMENT OF THE INTERIOR",
        "DEPARTMENT OF JUSTICE",
        "DEPARTMENT OF LABOR",
        "DEPARTMENT OF STATE",
        "DEPARTMENT OF TRANSPORTATION",
        "DEPARTMENT OF THE TREASURY",
        "DEPARTMENT OF VETERANS AFFAIRS",
    }
)


def _canon(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().upper().rstrip(",;"))


def named_split(upper: str, lower: str) -> bool:
    """True when a named exception says two stacked heading lines are two headings."""
    return _canon(lower) in GENERIC_SUBACCOUNTS or _canon(upper) in DEPARTMENTS
