"""One parse per bill XML per test process, shared by the suites that only read the tree.

The corpus gates parse the same committed versions over and over: each parametrized case,
and each module, called ``normalize_bill`` on its own, so an omnibus like 118-hr-4366 was
parsed dozens of times per worker. ``parsed_bill`` hands every caller in a process the
same ``BillTree``. Session fixtures in ``tests/conftest.py`` did this for a few HR 4366
versions; this does it for any path.

Sharing is safe only while no caller mutates the tree. ``BillTree`` and ``BillNode`` are
frozen, but ``BillTree.nodes`` is a plain list, so a caller that sorted, popped or
appended to it would change what every later caller sees, and their assertions would run
against a tree the parser never produced. ``parsed_bill`` checks the list against a
snapshot on every read and raises instead of serving a changed tree. Use
``normalize_bill`` directly in a test that needs a tree it may change, or one parsed under
a monkeypatched parser: a memoized tree was built before the patch and would hide it.

The key carries the file's mtime and size so a file rewritten during the run is parsed
again rather than served from a stale entry.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from deltatrack.bill_tree import BillNode, BillTree, normalize_bill


def parsed_bill(xml_path: Path) -> BillTree:
    """``normalize_bill(xml_path)``, parsed at most once per process for unchanged bytes."""
    path = Path(xml_path)
    stat = path.stat()
    tree, nodes_as_parsed = _parse(path, stat.st_mtime_ns, stat.st_size)
    if tuple(tree.nodes) != nodes_as_parsed:
        raise AssertionError(
            f"a test changed the node list of the shared tree for {path}; every later reader "
            "would see that change. Call normalize_bill for a private tree instead."
        )
    return tree


@lru_cache(maxsize=None)
def _parse(path: Path, _mtime_ns: int, _size: int) -> tuple[BillTree, tuple[BillNode, ...]]:
    tree = normalize_bill(path)
    return tree, tuple(tree.nodes)
