"""Regression guard for issue #380 -- LLAMA_SERVER_PATH silently breaks
every Windows load.

A fresh clone's agent resolves ``LLAMA_SERVER_PATH`` to the code default
``~/.local/bin/llama-server`` (``llauncher/core/settings.py``), which does
not exist on Windows -- ``POST /start`` fails loud with "Server binary not
found", but nothing in the setup path (README, installer) ever told a
Windows operator the var exists or where to set it. Before this fix, the
only mention lived in the commented example inside
``scripts/windows/agent.env.example`` (added for issue #123) -- easy to
miss on a first read.

This pins two independent surfaces named in the issue's acceptance
criteria:
  1. README.md calls out ``LLAMA_SERVER_PATH`` in the Windows setup path,
     naming both the dev (``run.bat``) and service-install
     (``install.ps1``) config channels.
  2. ``install.ps1`` prints a reminder on every run when the live
     ``agent.env`` has no active ``LLAMA_SERVER_PATH=`` line -- loud, not
     silent -- without flipping the template's var into an active
     assignment (that stays commented/opt-in per #123; see
     ``test_windows_agent_env_example_docs.py``).

The issue's third option -- a fail-loud install-door check that the
resolved binary actually exists -- is explicitly deferred (soft "consider"
language in the issue, not settled acceptance criteria); it is intentionally
NOT implemented or tested here.
"""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


README = _repo_root() / "README.md"
INSTALL_PS1 = _repo_root() / "scripts" / "windows" / "install.ps1"
WINDOWS_ENV_EXAMPLE = _repo_root() / "scripts" / "windows" / "agent.env.example"


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _install_ps1_text() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


# --- README ----------------------------------------------------------


def test_readme_windows_section_mentions_llama_server_path():
    """The Windows Notes section (read before Quick Start) must name
    LLAMA_SERVER_PATH explicitly, not bury it only in a linked doc."""
    text = _readme_text()
    windows_notes_idx = text.index("### Windows Notes")
    quick_start_idx = text.index("## Quick Start")
    assert quick_start_idx > windows_notes_idx, (
        "expected '## Quick Start' to follow '### Windows Notes' in README.md; "
        "section layout changed, update this test's anchors"
    )
    windows_section = text[windows_notes_idx:quick_start_idx]
    assert "LLAMA_SERVER_PATH" in windows_section, (
        "README.md's Windows Notes section must call out LLAMA_SERVER_PATH "
        "(issue #380) -- an unset default silently breaks the first /start "
        "on every fresh Windows install."
    )


def test_readme_names_both_config_channels():
    """The callout must point at both the dev (.env) and service
    (agent.env / install.ps1) channels -- a Windows operator following
    either path needs the pointer."""
    text = _readme_text()
    windows_notes_idx = text.index("### Windows Notes")
    quick_start_idx = text.index("## Quick Start")
    windows_section = text[windows_notes_idx:quick_start_idx]
    assert ".env.example" in windows_section, (
        "expected the dev-path .env.example to be named in the Windows "
        "LLAMA_SERVER_PATH callout"
    )
    assert "agent.env.example" in windows_section, (
        "expected the service-path agent.env.example to be named in the "
        "Windows LLAMA_SERVER_PATH callout"
    )


# --- install.ps1 -------------------------------------------------------


def test_install_ps1_warns_when_llama_server_path_unset():
    """install.ps1 must check the live agent.env for an active
    LLAMA_SERVER_PATH= line and warn (not silently proceed) if absent."""
    text = _install_ps1_text()
    assert re.search(r"LLAMA_SERVER_PATH", text), (
        "install.ps1 must reference LLAMA_SERVER_PATH (issue #380)"
    )
    assert re.search(r"Warn\s+[\"']LLAMA_SERVER_PATH", text), (
        "install.ps1 must emit a Warn (loud) message when LLAMA_SERVER_PATH "
        "is unset in the live agent.env, mirroring the existing loud-on-skip "
        "posture (issue #284) rather than staying silent."
    )


def test_install_ps1_reminder_checks_live_env_file():
    """The check must read the live $EnvFile (not the .example template)
    -- only the live file is what NSSM actually injects."""
    text = _install_ps1_text()
    match = re.search(
        r"\$llamaPathLine\s*=\s*\(Get-Content\s+\$EnvFile\)", text
    )
    assert match, (
        "expected install.ps1 to derive the LLAMA_SERVER_PATH presence "
        "check from `Get-Content $EnvFile` (the live agent.env), matching "
        "the pattern already used for the LLAUNCHER_AGENT_TOKEN check."
    )


def test_install_ps1_reminder_does_not_gate_install():
    """This is an informational reminder only -- it must not `Die` (abort
    the install). The fail-loud install-door check is issue #380's
    deferred third option, not settled acceptance criteria."""
    text = _install_ps1_text()
    warn_match = re.search(r'Warn\s+"LLAMA_SERVER_PATH[^\n]*', text)
    assert warn_match, "expected the LLAMA_SERVER_PATH Warn line to be present"
    # Look at a small window after the Warn call for a `Die` call that
    # would indicate this got wired as a hard gate instead of a reminder.
    window = text[warn_match.end() : warn_match.end() + 400]
    assert "Die " not in window and "Die(" not in window, (
        "the LLAMA_SERVER_PATH check must not call Die -- it is a loud "
        "reminder, not an install-blocking gate (that gate is issue #380's "
        "deferred third option)."
    )


def test_install_ps1_reminder_does_not_flip_template_to_active():
    """Guard against accidentally solving 'prompt/set it' by writing an
    active LLAMA_SERVER_PATH= line into agent.env.example -- issue #123's
    guard (test_windows_agent_env_example_docs.py) requires it stay a
    commented, opt-in example."""
    example_text = WINDOWS_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert not re.search(r"^LLAMA_SERVER_PATH=", example_text, re.MULTILINE), (
        "scripts/windows/agent.env.example must not gain an active "
        "LLAMA_SERVER_PATH= assignment -- it must stay commented/opt-in "
        "(issue #123)."
    )
