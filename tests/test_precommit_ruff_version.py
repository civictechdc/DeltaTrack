"""Guardrail that pre-commit and CI run the same Ruff (#646, #743).

CI runs ``uv run ruff``, which resolves the exact ``ruff==`` pin in ``pyproject.toml``. A hook
taken from the ``astral-sh/ruff-pre-commit`` repo would instead install into its own
virtualenv whatever release its ``rev`` tag declares: a second copy of the version, which
Dependabot's ``uv`` ecosystem never moves. Running the hooks through ``uv run`` leaves the pin
as the only copy, so there is nothing to fall out of step.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
PRECOMMIT = ROOT / ".pre-commit-config.yaml"

EXPECTED_ENTRIES = {
    "ruff-check": "uv run ruff check",
    "ruff-format": "uv run ruff format",
}


def _ruff_hooks() -> list[tuple[str, dict]]:
    config = yaml.safe_load(PRECOMMIT.read_text(encoding="utf-8"))
    return [
        (repo.get("repo", ""), hook)
        for repo in config.get("repos", [])
        for hook in repo.get("hooks", [])
        if "ruff" in repo.get("repo", "") or "ruff" in hook.get("id", "")
    ]


def test_precommit_runs_the_pinned_ruff_through_uv() -> None:
    """Every Ruff hook is a local `uv run ruff` command, so its version is the `pyproject.toml` pin."""
    hooks = _ruff_hooks()
    for repo, hook in hooks:
        hook_id = hook.get("id")
        assert repo == "local", (
            f"Ruff hook {hook_id!r} comes from {repo}, which installs its own Ruff release. "
            f"Use a `repo: local` hook running `uv run ruff` so CI's pin is the only version."
        )
        assert hook_id in EXPECTED_ENTRIES, f"unexpected Ruff hook id {hook_id!r} in {PRECOMMIT.name}"
        assert hook.get("entry", "").startswith(EXPECTED_ENTRIES[hook_id]), (
            f"Ruff hook {hook_id!r} runs {hook.get('entry')!r}; expected it to start with {EXPECTED_ENTRIES[hook_id]!r}"
        )
    # Without this the loop above passes vacuously on a config with the hooks removed.
    ids = {hook.get("id") for _, hook in hooks}
    assert ids == set(EXPECTED_ENTRIES), (
        f"expected Ruff hooks {sorted(EXPECTED_ENTRIES)} in {PRECOMMIT.name}, found {sorted(ids)}"
    )
