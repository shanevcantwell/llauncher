"""Guards for the agent's --user unit pinned-venv fail-loud backstop (#360).

#357 ratified Option A: the systemd deployment (agent + UI --user units) runs
from a unique, PINNED venv (``/opt/llauncher/venv``) independent of any dev
checkout's working-tree state. ``llauncher-agent.service.in`` (the --user
template) now resolves ``ExecStart`` through
``/usr/local/bin/llauncher-agent -> /opt/llauncher/venv/bin/llauncher-agent``
instead of ``@VENV_BIN@/llauncher-agent`` (this checkout's dev ``.venv``).

Mirrors ``tests/unit/test_ui_venv_backstop.py`` (ADR-023 Phase B, issue #228)
for the UI unit — same shape, same invariant (VENV-OWNED-OR-GUARANTEED /
PARSE-AT-THE-DOOR), applied to the agent's --user template.

Two surfaces are exercised:

1. Static wiring — the ``ExecStartPre`` backstop is present, fail-loud (no
   leading ``-``), keys on ``/opt/llauncher/venv`` + the entry point, names
   the real remediation, ``ExecStart`` itself resolves through the
   ``/usr/local/bin`` symlink (never ``@VENV_BIN@``/the dev checkout), and
   the unit takes on no cross-scope dependency on a system ensure unit.

2. Behavior — the actual ``ExecStartPre`` shell command, extracted from the
   template and run against a hermetic fake ``/opt`` tree, no-ops when the
   entry point is present and fails loud when the venv or entry point is
   missing/unusable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Walk up from this file until a ``.git`` entry is found."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


SYSTEMD_DIR = _repo_root() / "scripts" / "systemd"
AGENT_UNIT = SYSTEMD_DIR / "llauncher-agent.service.in"


def _directive_lines(text: str) -> list[str]:
    """Non-comment unit lines (comments start with ';')."""
    return [ln.strip() for ln in text.splitlines() if not ln.lstrip().startswith(";")]


def _exec_start_pre_lines(text: str) -> list[str]:
    return [ln for ln in _directive_lines(text) if ln.startswith("ExecStartPre=")]


def _exec_start_line(text: str) -> str:
    lines = [ln for ln in _directive_lines(text) if ln.startswith("ExecStart=")]
    assert len(lines) == 1, lines
    return lines[0]


# ───────────────────────── static unit wiring ──────────────────────────


def test_exec_start_resolves_through_usr_local_bin_symlink():
    """ExecStart never points at @VENV_BIN@/the dev checkout's .venv."""
    line = _exec_start_line(AGENT_UNIT.read_text())
    assert line == "ExecStart=/usr/local/bin/llauncher-agent", line
    assert "@VENV_BIN@" not in AGENT_UNIT.read_text()
    assert ".venv" not in line


def test_backstop_execstartpre_present():
    """The agent --user unit carries exactly one ExecStartPre backstop."""
    pre = _exec_start_pre_lines(AGENT_UNIT.read_text())
    assert len(pre) == 1, pre


def test_backstop_checks_shared_venv_and_entrypoint():
    """It verifies BOTH the shared venv dir AND the llauncher-agent entry point."""
    line = _exec_start_pre_lines(AGENT_UNIT.read_text())[0]
    assert "test -d /opt/llauncher/venv" in line
    assert "test -x /opt/llauncher/venv/bin/llauncher-agent" in line


def test_backstop_is_fail_loud_not_best_effort():
    """No leading '-' on the directive: a nonzero check MUST fail the unit."""
    line = _exec_start_pre_lines(AGENT_UNIT.read_text())[0]
    assert not line.startswith("ExecStartPre=-")
    assert "exit 1" in line


def test_backstop_names_real_remediation_command():
    """The journal message points at the REAL, existing root recompose step."""
    line = _exec_start_pre_lines(AGENT_UNIT.read_text())[0]
    assert "sudo bash scripts/systemd/install-cli.sh" in line
    assert (SYSTEMD_DIR / "install-cli.sh").exists()


def test_backstop_message_goes_to_stderr():
    """Fail-loud line must reach the journal via stderr."""
    line = _exec_start_pre_lines(AGENT_UNIT.read_text())[0]
    assert ">&2" in line


def test_agent_unit_takes_no_cross_scope_dependency():
    """A --user unit must NOT Requires=/After= a system ensure unit (forbidden)."""
    directives = _directive_lines(AGENT_UNIT.read_text())
    assert not any(ln.startswith("Requires=") for ln in directives)
    assert not any(
        ln.startswith("After=") and "ensure-venv" in ln for ln in directives
    )
    line = _exec_start_pre_lines(AGENT_UNIT.read_text())[0]
    assert "install-cli.sh" in line  # present as guidance...
    assert "bash scripts/systemd/install-cli.sh" not in line.split("echo", 1)[0]


# ───────────────────────── behavior of the check ───────────────────────


def _backstop_command(text: str) -> str:
    """Extract the `/bin/sh -c '<cmd>'` payload from the ExecStartPre line."""
    line = _exec_start_pre_lines(text)[0]
    prefix = "ExecStartPre=/bin/sh -c "
    assert line.startswith(prefix), line
    payload = line[len(prefix):]
    assert payload.startswith("'") and payload.endswith("'"), payload
    return payload[1:-1]


def _run_backstop(cmd: str, fake_root: Path) -> subprocess.CompletedProcess[str]:
    """Run the backstop command with /opt rerooted under a tmp dir.

    The command hardcodes absolute /opt paths; we rewrite them to the fake
    root so the test is hermetic and touches no real system path — in
    particular never the real /opt/llauncher/venv (out of this issue's
    scope: composing it is a user:gate operator action, not this test's).
    """
    rerooted = cmd.replace("/opt/llauncher/venv", str(fake_root / "opt/llauncher/venv"))
    return subprocess.run(
        ["bash", "-c", rerooted],
        capture_output=True,
        text=True,
        check=False,
    )


def _make_entrypoint(fake_root: Path) -> Path:
    venv_bin = fake_root / "opt/llauncher/venv/bin"
    venv_bin.mkdir(parents=True)
    ep = venv_bin / "llauncher-agent"
    ep.write_text("#!/bin/sh\n")
    ep.chmod(0o755)
    return ep


def test_backstop_noop_when_entrypoint_present(tmp_path: Path):
    """Present, executable entry point => clean exit, no message."""
    cmd = _backstop_command(AGENT_UNIT.read_text())
    _make_entrypoint(tmp_path)

    result = _run_backstop(cmd, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.strip() == ""


def test_backstop_fails_loud_when_venv_missing(tmp_path: Path):
    """No /opt venv at all => nonzero + remediation on stderr."""
    cmd = _backstop_command(AGENT_UNIT.read_text())

    result = _run_backstop(cmd, tmp_path)

    assert result.returncode != 0
    assert "install-cli.sh" in result.stderr


def test_backstop_fails_loud_when_entrypoint_missing(tmp_path: Path):
    """Venv dir exists but the entry point does not => fail loud."""
    cmd = _backstop_command(AGENT_UNIT.read_text())
    (tmp_path / "opt/llauncher/venv").mkdir(parents=True)

    result = _run_backstop(cmd, tmp_path)

    assert result.returncode != 0
    assert "install-cli.sh" in result.stderr


def test_backstop_fails_loud_when_entrypoint_not_executable(tmp_path: Path):
    """Entry point present but not executable => 'usable' check fails loud."""
    cmd = _backstop_command(AGENT_UNIT.read_text())
    ep = _make_entrypoint(tmp_path)
    ep.chmod(0o644)

    result = _run_backstop(cmd, tmp_path)

    assert result.returncode != 0
    assert "install-cli.sh" in result.stderr
