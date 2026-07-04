"""Regression guard for issue #123 — LLAMA_SERVER_PATH documented in the
Windows agent env template.

``scripts/windows/llauncher-agent.env.example`` is the source
``install.ps1`` copies into ``%USERPROFILE%\\.llauncher\\agent.env`` and
feeds to NSSM's ``AppEnvironmentExtra``. Before this fix, the template
covered auth/network/identity vars but never mentioned
``LLAMA_SERVER_PATH`` (or ``SCRIPTS_PATH``), so an operator installing
the service under a non-default llama-server location — or under
NSSM's default LocalSystem account, whose home does not resolve to the
operator's own ``~/.local/bin`` — had no documented override channel in
the file NSSM actually injects.

This pins:
  1. Both vars are present as commented (opt-in) examples, matching the
     "commented example, uncomment to override" shape already used by
     the other windows/systemd templates.
  2. The line is a genuine comment, not an active assignment — this
     file must not silently start setting ``LLAMA_SERVER_PATH`` for
     every fresh install (that stays the code default in
     ``llauncher/core/settings.py``).
  3. The systemd sibling template already documents ``LLAMA_SERVER_PATH``
     (added for issue #195) — this guards the two per-platform templates
     from drifting back out of parity on this variable.
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


WINDOWS_ENV_EXAMPLE = _repo_root() / "scripts" / "windows" / "llauncher-agent.env.example"
SYSTEMD_ENV_EXAMPLE = _repo_root() / "scripts" / "systemd" / "llauncher-agent.env.example"


@pytest.fixture
def windows_env_text() -> str:
    return WINDOWS_ENV_EXAMPLE.read_text(encoding="utf-8")


@pytest.mark.parametrize("var_name", ["LLAMA_SERVER_PATH", "SCRIPTS_PATH"])
def test_var_documented_as_commented_example(windows_env_text: str, var_name: str) -> None:
    """Each var appears as a commented-out ``# NAME=...`` example line."""
    pattern = re.compile(rf"^#\s*{re.escape(var_name)}=\S", re.MULTILINE)
    assert pattern.search(windows_env_text), (
        f"{var_name} is not documented as a commented example in "
        f"{WINDOWS_ENV_EXAMPLE}"
    )


@pytest.mark.parametrize("var_name", ["LLAMA_SERVER_PATH", "SCRIPTS_PATH"])
def test_var_not_active_by_default(windows_env_text: str, var_name: str) -> None:
    """The new guidance must not flip these into live assignments — a
    fresh install should still fall back to the code default in
    ``llauncher/core/settings.py``, not a value baked into the template.
    """
    pattern = re.compile(rf"^{re.escape(var_name)}=", re.MULTILINE)
    assert not pattern.search(windows_env_text), (
        f"{var_name} must stay commented-out in the .example template; "
        "found an active (uncommented) assignment."
    )


def test_llama_server_path_documented_on_both_platform_templates() -> None:
    """Windows and systemd templates stay in parity on this variable."""
    systemd_text = SYSTEMD_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "LLAMA_SERVER_PATH" in systemd_text, (
        "expected the systemd template (added for #195) to already "
        "document LLAMA_SERVER_PATH; if this fails the fixture repo "
        "state has changed and the parity guard needs updating"
    )
