"""Regression guards for issue #501: README Quick Start verbs must exist in
BOTH runner scripts' dispatch tables, and the runner-invocation examples
must use the real `scripts/` path — not a bare `run.sh` / `run.bat` that
"not recognized"s from the repo root.

Background
----------
The README's Quick Start documented `./run.sh agent` / `run.bat agent-bg`
as if the scripts lived at the repo root. They live at `scripts/run.sh` /
`scripts\\run.bat`; from the root, the bare form fails ("not recognized" on
Windows, "command not found" on a POSIX shell with `.` not on PATH).
`agent-bg` compounds this: it was removed from BOTH scripts in 0c75c67
(2026-06-06, "drop agent-bg") but the README kept documenting it on both
platforms.

This module (the #304 script-dispatch test lane) pins two things so this
class of drift fails CI instead of shipping silently:

1. Every verb the README's Windows Quick Start block names is an actual
   dispatch case in ``scripts/run.bat`` (and symmetrically for
   ``scripts/run.sh``'s Linux/macOS block) — a README verb with no matching
   script case is exactly the kind of stale-doc gap #501 found.
2. `agent-bg` does not reappear in the README's Quick Start blocks, since
   neither script implements it (and re-adding it to one script without the
   other would violate script parity, which the #501 fix asserts here too).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


def _readme_text() -> str:
    return (_repo_root() / "README.md").read_text(encoding="utf-8")


def _quick_start_block(readme: str, heading: str) -> str:
    """Return the fenced code block immediately following ``heading`` inside
    the '## Quick Start' section (e.g. '**Windows:**' or '**Linux/macOS:**')."""
    qs_start = readme.index("## Quick Start")
    qs_end = readme.index("###", qs_start)
    section = readme[qs_start:qs_end]
    head_idx = section.index(heading)
    fence_start = section.index("```", head_idx)
    fence_start = section.index("\n", fence_start) + 1
    fence_end = section.index("```", fence_start)
    return section[fence_start:fence_end]


def _readme_verbs(block: str) -> set[str]:
    """The verb token of each invocation line: the whitespace-delimited
    token right after the ``scripts/run.sh`` / ``scripts\\run.bat`` prefix."""
    verbs = set()
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("::") or line.startswith("#"):
            continue
        tokens = line.split()
        assert tokens[0] in ("./scripts/run.sh", "scripts\\run.bat"), (
            f"unexpected invocation prefix in Quick Start line: {line!r}"
        )
        verbs.add(tokens[1])
    return verbs


def _run_bat_dispatch_verbs() -> set[str]:
    text = (_repo_root() / "scripts" / "run.bat").read_text(encoding="utf-8")
    return set(re.findall(r'if /i "%~1"=="([\w-]+)" goto', text))


def _run_sh_dispatch_verbs() -> set[str]:
    text = (_repo_root() / "scripts" / "run.sh").read_text(encoding="utf-8")
    # Case labels are 4-space-indented "verb)" lines inside the `case` block.
    labels = set(re.findall(r"^ {4}([\w-]+)\)$", text, re.MULTILINE))
    labels.discard("*")
    return labels


README = _readme_text()
WINDOWS_README_VERBS = _readme_verbs(_quick_start_block(README, "**Windows:**"))
LINUX_README_VERBS = _readme_verbs(_quick_start_block(README, "**Linux/macOS:**"))


def test_windows_quick_start_names_at_least_one_verb():
    # Guard against the extraction itself silently finding nothing.
    assert WINDOWS_README_VERBS


def test_linux_quick_start_names_at_least_one_verb():
    assert LINUX_README_VERBS


def test_every_readme_windows_verb_exists_in_run_bat_dispatch():
    dispatch_verbs = _run_bat_dispatch_verbs()
    missing = WINDOWS_README_VERBS - dispatch_verbs
    assert not missing, (
        f"README documents run.bat verb(s) {sorted(missing)} that "
        f"scripts/run.bat does not dispatch"
    )


def test_every_readme_linux_verb_exists_in_run_sh_dispatch():
    dispatch_verbs = _run_sh_dispatch_verbs()
    missing = LINUX_README_VERBS - dispatch_verbs
    assert not missing, (
        f"README documents run.sh verb(s) {sorted(missing)} that "
        f"scripts/run.sh does not dispatch"
    )


def test_agent_bg_absent_from_readme_quick_start_verbs():
    """agent-bg was removed from both scripts (0c75c67); it must not be
    re-documented as an invocable verb in either Quick Start block."""
    assert "agent-bg" not in WINDOWS_README_VERBS
    assert "agent-bg" not in LINUX_README_VERBS


@pytest.mark.parametrize("path", ["scripts/run.sh", "scripts/run.bat"])
def test_agent_bg_absent_from_scripts(path: str):
    """Neither script implements agent-bg; it must not be reintroduced in
    only one of them (script parity)."""
    text = (_repo_root() / path).read_text(encoding="utf-8")
    assert "agent-bg" not in text


def test_readme_quick_start_uses_scripts_prefix():
    """The invocation examples must name the real scripts/ location, not a
    bare run.sh / run.bat that only resolves from inside scripts/."""
    windows_block = _quick_start_block(README, "**Windows:**")
    linux_block = _quick_start_block(README, "**Linux/macOS:**")
    assert "scripts\\run.bat" in windows_block
    assert "scripts/run.sh" in linux_block
    # And nothing in either block still names the bare (repo-root-relative) form.
    assert not re.search(r"(?<!scripts.)(?<!scripts/)\brun\.bat\b", windows_block)
    assert not re.search(r"(?<!scripts/)\./run\.sh\b", linux_block)
