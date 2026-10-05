"""The `browser` tier fails closed under `--run-browser` (#599).

CI's dedicated browser step exists to run the Playwright tests with Chromium
present. Its launch helper — the module-scoped `chromium` fixture in each browser
module — skips when the browser can't start: the right behavior for the default
`-m "not slow and not browser"` tier, where a contributor's machine may lack
Playwright, but a silent no-op under that CI step, where every test "passes" by
skipping and the step reports green while asserting nothing.

`--run-browser` is the CI step's signal (ci.yml): when set, a launch failure
raises instead of skipping, so a drifted or uninstallable Chromium reddens CI
instead of being reported as a clean pass. These tests prove the flag flips each
module's fixture from skip to raise, in both directions — a guard that has never
fired in the raise direction cannot distinguish "Chromium is fine" from "the
guard is broken".

The module-level ``importorskip("playwright")`` needs no counterpart here: it
guards the Python *package*, which CI's preceding ``playwright install chromium``
step would already fail loudly on if missing (#599).
"""

import pytest

from tests import test_frontend_browser as frontend

_BROWSER_MODULES = (frontend,)


class _Config:
    def __init__(self, run_browser):
        self._run_browser = run_browser

    def getoption(self, name):
        assert name == "--run-browser", name
        return self._run_browser


class _Request:
    def __init__(self, run_browser):
        self.config = _Config(run_browser)


class _FakeChromium:
    def __init__(self, exc):
        self._exc = exc

    def launch(self):
        raise self._exc


class _FakePlaywright:
    """A Playwright whose entry succeeds but whose browser launch fails.

    The failure the fixture guards against is the *launch* — a missing or drifted
    Chromium binary — not a broken Playwright entrypoint. Raising from
    ``sync_playwright()`` itself would short-circuit before ``p.chromium.launch()``,
    exercising a path the fixture was never written for; raising at ``launch()`` is
    the exact point a real-world failure lands on.
    """

    def __init__(self, exc):
        self._exc = exc
        self.chromium = _FakeChromium(exc)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _force_launch_failure(monkeypatch, module, exc):
    """Make ``p.chromium.launch()`` raise, as a real missing/drifted binary does."""

    def fake_playwright():
        return _FakePlaywright(exc)

    monkeypatch.setattr(module, "sync_playwright", fake_playwright)


@pytest.mark.parametrize(
    "module",
    _BROWSER_MODULES,
    ids=lambda m: m.__name__.rsplit(".", 1)[-1],
)
def test_launch_failure_skips_without_run_browser(module, monkeypatch):
    """Default tier: a Chromium that can't launch still skips, not fails."""
    _force_launch_failure(monkeypatch, module, RuntimeError("chromium binary missing"))
    fixture = module.chromium.__wrapped__
    with pytest.raises(pytest.skip.Exception):
        next(fixture(_Request(run_browser=False)))


@pytest.mark.parametrize(
    "module",
    _BROWSER_MODULES,
    ids=lambda m: m.__name__.rsplit(".", 1)[-1],
)
def test_launch_failure_raises_under_run_browser(module, monkeypatch):
    """Browser-tier strictness: the same failure reddens the test instead of hiding it."""
    _force_launch_failure(monkeypatch, module, RuntimeError("chromium binary missing"))
    fixture = module.chromium.__wrapped__
    with pytest.raises(RuntimeError, match="chromium binary missing"):
        next(fixture(_Request(run_browser=True)))
