"""A synthetic report with removed changes, and a reader for its Changes view (#784).

Shared by the removed-section unit tests, the corpus gates and the browser test, so
each asserts on one reading of the rendered structure rather than its own.
"""

from __future__ import annotations

from html.parser import HTMLParser


def node(label, level, span, children=()):
    return {
        "label": label,
        "level": level,
        "own_amounts": [],
        "full_text_span": span,
        "children": list(children),
    }


def span(start, end):
    return {"start": start, "end": end}


def change(cid, change_type, *, v1_path=None, v2_path=None, v1=None, v2=None):
    return {
        "id": cid,
        "change_type": change_type,
        "section_number": "",
        "path": {"v1": v1_path, "v2": v2_path},
        "location": None,
        "anchor_resolution": "resolved",
        "text": {"old": f"old {cid}", "new": f"new {cid}"},
        "move": None,
        "full_text_span": {"v1": v1, "v2": v2},
    }


def tree_v1():
    return [
        node(
            "TITLE I",
            "title",
            span(0, 7),
            [
                node("OLD ACCOUNT", "account", span(10, 40)),
                node("KEPT ACCOUNT", "account", span(50, 90)),
            ],
        ),
        node("TITLE II", "title", span(100, 108), [node("KEPT ACCOUNT", "account", span(110, 150))]),
    ]


def tree_v2(title_one="TITLE I"):
    # The later version repeats "KEPT ACCOUNT" under both titles: a label match
    # cannot tell them apart, an exact full path can.
    return [
        node(title_one, "title", span(0, 7), [node("KEPT ACCOUNT", "account", span(10, 50))]),
        node("TITLE II", "title", span(60, 68), [node("KEPT ACCOUNT", "account", span(70, 110))]),
    ]


def changes(title_one="TITLE I"):
    return [
        change(
            "c-0001",
            "modified",
            v1_path=["TITLE I", "KEPT ACCOUNT"],
            v2_path=[title_one, "KEPT ACCOUNT"],
            v1=span(60, 70),
            v2=span(20, 30),
        ),
        change(
            "c-0002",
            "modified",
            v1_path=["TITLE II", "KEPT ACCOUNT"],
            v2_path=["TITLE II", "KEPT ACCOUNT"],
            v1=span(120, 130),
            v2=span(80, 90),
        ),
        # Parent TITLE I > KEPT ACCOUNT survives exactly: pointer from that group only,
        # never from TITLE II > KEPT ACCOUNT, which shares the deepest label.
        change("c-0003", "removed", v1_path=["TITLE I", "KEPT ACCOUNT", "(a)"], v1=span(70, 80)),
        # Leaf gone, parent TITLE I survives.
        change("c-0004", "removed", v1_path=["TITLE I", "OLD ACCOUNT"], v1=span(10, 40)),
        # No earlier breadcrumb at all.
        change("c-0005", "removed", v1_path=None),
    ]


def _with_ids(nodes, side):
    """Give every node its preorder identifier, as the producer does (#785), and
    return a map from each node's label path to its id."""
    by_path: dict[tuple[str, ...], str | None] = {}
    counter = 0

    def walk(ns, path):
        nonlocal counter
        for n in ns:
            n["id"] = f"{side}.{counter}"
            counter += 1
            p = (*path, n["label"]) if n["label"] else path
            # A repeated label path names no single node: resolving a change against
            # it would pick one silently, the collision #785 exists to remove.
            by_path[p] = None if p in by_path else n["id"]
            walk(n["children"], p)

    walk(nodes, ())
    return by_path


def canonical(change_list=None, *, later_tree=None):
    """A document whose changes name their nodes, the way a producer states them.

    The builder resolves each change's node from its path, and refuses a path that
    names two nodes; a producer resolves it from the parse (#785). A change that
    already carries ``node`` keeps it.
    """
    trees = {"v1": tree_v1(), "v2": tree_v2() if later_tree is None else later_tree}
    ids = {side: _with_ids(trees[side], side) for side in ("v1", "v2")}
    change_list = changes() if change_list is None else change_list

    def resolve(side, path):
        key = tuple(path or ())
        if key in ids[side] and ids[side][key] is None:
            raise ValueError(f"{side} path {key} names two nodes; give the change an explicit node")
        return ids[side].get(key)

    for c in change_list:
        if "node" not in c:
            v1_applies = c["change_type"] in ("removed", "modified", "moved")
            v2_applies = c["change_type"] in ("added", "modified", "moved")
            c["node"] = {
                "v1": resolve("v1", c["path"]["v1"]) if v1_applies else None,
                "v2": resolve("v2", c["path"]["v2"]) if v2_applies else None,
            }
    return {
        "schema_version": "3.1",
        "bill": {"type": "hr", "number": 1, "congress": 119},
        "versions": {
            "v1": {"label": "v1", "version_number": 1, "source": "xml"},
            "v2": {"label": "v2", "version_number": 2, "source": "xml"},
        },
        "summary": {"added": 0, "removed": 3, "modified": 2, "moved": 0},
        "changes": change_list,
        "tree": trees,
    }


class ChangesView(HTMLParser):
    """Where each card and pointer sits in the Changes view.

    ``cards``: per ``change-N`` card, the summary labels of its ``change-group``
    ancestors outermost first (the removed section's own label left out) and whether
    it is inside the removed section. ``pointers``: per later-version group carrying
    a ``removed-pointer``, the removed-section group its link targets, as that
    group's breadcrumb, with the count the pointer states.
    """

    def __init__(self):
        super().__init__()
        self.stack: list[dict] = []
        self.cards: dict[int, dict] = {}
        self._removed_groups: dict[str, tuple[str, ...]] = {}
        self._pointer_links: list[tuple[tuple[str, ...], str, int]] = []
        self._in_summary: dict | None = None
        self._pointer_link: list | None = None
        self._in_pointer = False

    @property
    def pointer_list(self) -> list[tuple[tuple[str, ...], tuple[tuple[str, ...], int]]]:
        """Every pointer in page order, duplicates kept."""
        return [(path, (self._removed_groups[target], count)) for path, target, count in self._pointer_links]

    @property
    def pointers(self) -> dict[tuple[str, ...], tuple[tuple[str, ...], int]]:
        """Pointers by later group path. Fails if two groups at one path both carry one,
        rather than letting a dict keep only the last."""
        paths = [path for path, _ in self.pointer_list]
        assert len(paths) == len(set(paths)), f"two pointers from one label path: {paths}"
        return dict(self.pointer_list)

    def _path(self) -> tuple[str, ...]:
        return tuple(g["label"] for g in self.stack if g["group"] and not g["removed"])

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "details":
            self.stack.append(
                {
                    "group": "change-group" in classes,
                    "removed": "removed-section" in classes,
                    "label": "",
                    "id": a.get("id"),
                    "node": a.get("data-node"),
                }
            )
        elif tag == "summary" and self.stack:
            self._in_summary = self.stack[-1]
        elif tag == "div" and "change" in classes and (a.get("id") or "").startswith("change-"):
            self.cards[int(a["id"].split("-")[1])] = {
                "path": self._path(),
                "nodes": tuple(g["node"] for g in self.stack if g["group"] and g["node"]),
                "in_removed": any(g["removed"] for g in self.stack),
            }
        elif tag == "a" and self.stack and self._pointer_link is None and self._in_pointer:
            self._pointer_link = [self._path(), a["href"].lstrip("#"), ""]
        elif tag == "p" and "removed-pointer" in classes:
            self._in_pointer = True

    def handle_endtag(self, tag):
        if tag == "details" and self.stack:
            self.stack.pop()
        elif tag == "summary" and self._in_summary is not None:
            group = self._in_summary
            if group["id"] and any(g["removed"] for g in self.stack):
                self._removed_groups[group["id"]] = self._path()
            self._in_summary = None
        elif tag == "a" and self._pointer_link is not None:
            path, target, text = self._pointer_link
            self._pointer_links.append((path, target, int(text.split()[0])))
            self._pointer_link = None
        elif tag == "p":
            self._in_pointer = False

    def handle_data(self, data):
        if self._in_summary is not None:
            self._in_summary["label"] += data
        if self._pointer_link is not None:
            self._pointer_link[2] += data


def changes_view(html: str) -> ChangesView:
    changes_view = html.split('<div class="view view-full"', 1)[0]
    parser = ChangesView()
    parser.feed(changes_view)
    return parser


def removed_section(html: str) -> str:
    start = html.index('<details class="change-group removed-section"')
    return html[start : html.index('<p class="filter-empty"', start)]
