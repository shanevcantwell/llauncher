"""Static + behavioral guard: coverage.py reconciles worktree paths (#361).

Why this test exists
---------------------
The dev ``.venv``'s editable install (``_editable_impl_llauncher.pth``)
always resolves ``import llauncher`` to whichever checkout it was last
``pip install -e``'d from — normally the main checkout — regardless of
which worktree pytest is invoked from. When a worktree run's import
resolution loses that race, coverage.py's ``source = ["llauncher"]`` root
never matches the main checkout's absolute paths, and every file reads as
uncovered instead of erroring: a worktree run reports a plausible but
false near-0% number rather than the environment defect it is (observed
twice, PR #359 review / PR #222 conflict-resolution report).

``[tool.coverage.paths]`` is the persisted fix: it tells ``coverage
combine``/``coverage report`` to alias any of the listed source patterns
back onto the canonical ``llauncher/`` root before attributing lines, so a
worktree-style absolute path collapses onto the same module the main
checkout's relative path does. This test pins the mapping's shape
(``.claude/worktrees/*/llauncher`` and ``/tmp/wt-*/llauncher``, per the
issue's acceptance criteria) and behaviorally proves ``coverage.py`` itself
treats an aliased path as equivalent to the canonical one, rather than
merely asserting the config keys exist.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import coverage

# Repo root = two parents up from this file: tests/architecture/<file>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _load_pyproject() -> dict:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def test_coverage_paths_mapping_present() -> None:
    """``[tool.coverage.paths] source`` must alias the worktree patterns."""
    data = _load_pyproject()
    paths = data["tool"]["coverage"]["paths"]
    aliases = paths["source"]
    assert "llauncher" in aliases, (
        "[tool.coverage.paths] source is missing the canonical 'llauncher' "
        f"root: {aliases!r}"
    )
    assert ".claude/worktrees/*/llauncher" in aliases, (
        "[tool.coverage.paths] source is missing the "
        "'.claude/worktrees/*/llauncher' pattern the issue's acceptance "
        f"criteria requires: {aliases!r}"
    )
    assert "/tmp/wt-*/llauncher" in aliases, (
        "[tool.coverage.paths] source is missing the '/tmp/wt-*/llauncher' "
        f"scratch-worktree pattern the issue's acceptance criteria requires: {aliases!r}"
    )


def test_coverage_run_records_relative_files() -> None:
    """``relative_files`` must be on so recorded paths aren't checkout-absolute.

    Without this, a data file recorded under one checkout's absolute path
    can never match a ``[tool.coverage.paths]`` alias resolved against a
    different checkout's cwd.
    """
    data = _load_pyproject()
    run_cfg = data["tool"]["coverage"]["run"]
    assert run_cfg.get("relative_files") is True, (
        "[tool.coverage.run] relative_files must be true so worktree-run "
        "data files record paths relative to pyproject.toml, not absolute "
        "to whichever checkout collected them"
    )


def test_worktree_style_alias_resolves_onto_canonical_source_root() -> None:
    """Behavioral proof: coverage.py's own path-alias machinery accepts the mapping.

    Builds a ``coverage.Coverage`` instance from the repo's real
    ``pyproject.toml`` and uses its private ``_make_aliases()`` (the exact
    method ``combine``/``report`` call internally) to map a synthetic
    worktree-shaped path. It must collapse onto the same relative module
    path a plain ``llauncher/...`` path already uses — proving the mapping
    is wired, not just declared alongside dead config keys.

    ``relative_files = true`` (pinned above) means coverage.py records data
    with paths already relative to ``pyproject.toml``'s directory, so the
    realistic alias input is a relative worktree-shaped fragment
    (``.claude/worktrees/<name>/llauncher/...``), not an absolute one.
    """
    cov = coverage.Coverage(config_file=str(_PYPROJECT))
    aliases = cov._make_aliases()

    worktree_path = ".claude/worktrees/wf_scratch/llauncher/core/config.py"
    scratch_wt_path = "/tmp/wt-abc123/llauncher/core/config.py"
    canonical_path = "llauncher/core/config.py"

    # coverage.py's alias mapping returns OS-native separators; normalize
    # to forward slashes so the assertion holds on Windows too (#523).
    mapped_worktree = aliases.map(worktree_path).replace("\\", "/")
    mapped_scratch = aliases.map(scratch_wt_path).replace("\\", "/")

    assert mapped_worktree == canonical_path, (
        f"worktree-shaped path {worktree_path!r} mapped to "
        f"{mapped_worktree!r}, expected {canonical_path!r}"
    )
    assert mapped_scratch == canonical_path, (
        f"scratch-worktree path {scratch_wt_path!r} mapped to "
        f"{mapped_scratch!r}, expected {canonical_path!r}"
    )
