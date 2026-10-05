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


def canonical(change_list=None, *, later_tree=None):
    return {
        "schema_version": "3.1",
        "bill": {"type": "hr", "number": 1, "congress": 119},
        "versions": {
            "v1": {"label": "v1", "version_number": 1, "source": "xml"},
            "v2": {"label": "v2", "version_number": 2, "source": "xml"},
        },
        "summary": {"added": 0, "removed": 3, "modified": 2, "moved": 0},
        "changes": changes() if change_list is None else change_list,
        "tree": {"v1": tree_v1(), "v2": tree_v2() if later_tree is None else later_tree},
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
    def pointers(self) -> dict[tuple[str, ...], tuple[tuple[str, ...], int]]:
        return {path: (self._removed_groups[target], count) for path, target, count in self._pointer_links}

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
                }
            )
        elif tag == "summary" and self.stack:
            self._in_summary = self.stack[-1]
        elif tag == "div" and "change" in classes and (a.get("id") or "").startswith("change-"):
            self.cards[int(a["id"].split("-")[1])] = {
                "path": self._path(),
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
