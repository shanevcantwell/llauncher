"""Static guard: the ``.llauncher/`` state-dir pattern must actually ignore.

Why this test exists
---------------------
``.gitignore`` patterns do not perform tilde (``~``) expansion. A prior
version of this file read ``~/.llauncher/``, which only matches a literal
directory *named* ``~`` — i.e. it guarded nothing. A full copy of the
operator's live ``~/.llauncher`` state dir (including ``agent.token`` and
``audit.jsonl``) sat unignored under ``docs/handoffs/.llauncher/`` for
several days as a result, invisible to ``git status`` only because its
0700 perms turned the listing into a permission-denied warning (#252).

This test pins the fix (``.llauncher/`` — no tilde, matches at any depth)
by asking Git itself, via ``git check-ignore``, exactly as the issue's
acceptance criteria specify. It is a regression guard against the pattern
ever regaining a tilde or losing its match-at-any-depth shape.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Repo root = two parents up from this file: tests/architecture/<file>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_GITIGNORE = _REPO_ROOT / ".gitignore"


def test_gitignore_has_no_tilde_home_patterns() -> None:
    """No ``.gitignore`` line may rely on tilde expansion.

    Git never expands ``~`` in ignore patterns, so any such line matches
    only a literal directory named ``~`` and guards nothing.
    """
    lines = _GITIGNORE.read_text().splitlines()
    tilde_home_lines = [
        line for line in lines if re.match(r"^~[/\\]", line.strip())
    ]
    assert not tilde_home_lines, (
        "`.gitignore` contains tilde-anchored pattern(s) that gitignore "
        f"cannot expand and therefore never match: {tilde_home_lines!r}"
    )


def test_gitignore_llauncher_pattern_present_without_tilde() -> None:
    """The literal fix from #252: ``.llauncher/`` present, no tilde form."""
    text = _GITIGNORE.read_text()
    assert ".llauncher/" in text.splitlines()
    assert "~/.llauncher/" not in text


def test_llauncher_state_dir_ignored_at_any_depth(tmp_path: Path) -> None:
    """``git check-ignore`` must match a ``.llauncher/`` dir at repo root
    and nested arbitrarily deep — mirroring the exact probe from the
    issue's acceptance criteria (root case and the ``docs/handoffs/``
    depth where the real incident occurred).
    """
    probes = [
        _REPO_ROOT / ".llauncher",
        _REPO_ROOT / "docs" / "handoffs" / ".llauncher",
    ]
    created = []
    try:
        for probe in probes:
            probe.mkdir(parents=True, exist_ok=False)
            created.append(probe)

        result = subprocess.run(
            ["git", "check-ignore", *[str(p) for p in probes]],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
        )
        matched = set(result.stdout.splitlines())
        assert result.returncode == 0, (
            f"git check-ignore did not match all probes: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        for probe in probes:
            assert str(probe) in matched, f"{probe} was not reported ignored"
    finally:
        for probe in reversed(created):
            probe.rmdir()
