"""Guards for ADR-LLNCH-023 Phase B — UI shared-venv fail-loud backstop (issue #228).

The UI runs as a per-operator ``systemd --user`` unit whose ExecStart resolves
``/usr/local/bin/llauncher-ui`` -> ``/opt/llauncher/venv/bin/llauncher-ui``, a
shared ROOT-owned venv the operator's reap policy treats as non-durable. A
``--user`` unit cannot recompose that root tree (cross-scope forbidden, OQ1 Fork
B-shared), so the unit only DETECTS-and-FAILS-LOUD: an ``ExecStartPre`` that
verifies the shared venv and its ``llauncher-ui`` entry point exist and are
usable, and on failure exits nonzero (so the unit enters ``failed``) with a
journal line pointing at the REAL, existing root recompose command
(``sudo bash scripts/systemd/install-cli.sh``).

Two surfaces are exercised:

1. Static wiring — the ``ExecStartPre`` is present, fail-loud (no leading ``-``),
   keys on ``/opt/llauncher/venv`` + the entry point, names the real remediation,
   and the unit takes on NO cross-scope dependency on a system ensure unit.

2. Behavior — the actual ``ExecStartPre`` shell command, extracted from the
   template and run against a hermetic fake ``/opt`` tree, no-ops when the entry
   point is present and fails loud (nonzero + remediation message) when the venv
   or entry point is missing/unusable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "extracts and runs the UI systemd ExecStartPre backstop fragment "
        "via `bash -c` against a rerooted /opt/llauncher/venv; the systemd "
        "unit fragment and its POSIX paths don't apply on Windows"
    ),
)


def _repo_root() -> Path:
    """Walk up from this file until a ``.git`` entry is found."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


SYSTEMD_DIR = _repo_root() / "scripts" / "systemd"
UI_UNIT = SYSTEMD_DIR / "llauncher-ui.service.user.in"


def _directive_lines(text: str) -> list[str]:
    """Non-comment unit lines (comments start with ';')."""
    return [ln.strip() for ln in text.splitlines() if not ln.lstrip().startswith(";")]


def _exec_start_pre_lines(text: str) -> list[str]:
    return [ln for ln in _directive_lines(text) if ln.startswith("ExecStartPre=")]


# ───────────────────────── static unit wiring ──────────────────────────


def test_backstop_execstartpre_present():
    """The UI unit carries exactly one ExecStartPre backstop."""
    pre = _exec_start_pre_lines(UI_UNIT.read_text())
    assert len(pre) == 1, pre


def test_backstop_checks_shared_venv_and_entrypoint():
    """It verifies BOTH the shared venv dir AND the llauncher-ui entry point."""
    line = _exec_start_pre_lines(UI_UNIT.read_text())[0]
    assert "test -d /opt/llauncher/venv" in line
    assert "test -x /opt/llauncher/venv/bin/llauncher-ui" in line


def test_backstop_is_fail_loud_not_best_effort():
    """No leading '-' on the directive: a nonzero check MUST fail the unit."""
    line = _exec_start_pre_lines(UI_UNIT.read_text())[0]
    # 'ExecStartPre=-/bin/sh' would make it best-effort (ignored failure).
    assert not line.startswith("ExecStartPre=-")
    assert "exit 1" in line  # explicit nonzero on the failure branch


def test_backstop_names_real_remediation_command():
    """The journal message points at the REAL, existing root recompose step."""
    line = _exec_start_pre_lines(UI_UNIT.read_text())[0]
    assert "sudo bash scripts/systemd/install-cli.sh" in line
    # The named remediation must actually exist in the tree (not a dead pointer).
    assert (SYSTEMD_DIR / "install-cli.sh").exists()


def test_backstop_message_goes_to_stderr():
    """Fail-loud line must reach the journal via stderr."""
    line = _exec_start_pre_lines(UI_UNIT.read_text())[0]
    assert ">&2" in line


def test_ui_unit_takes_no_cross_scope_dependency():
    """A --user unit must NOT Requires=/After= a system ensure unit (forbidden)."""
    directives = _directive_lines(UI_UNIT.read_text())
    assert not any(ln.startswith("Requires=") for ln in directives)
    assert not any(
        ln.startswith("After=") and "ensure-venv" in ln for ln in directives
    )
    # And it must not try to invoke the root recompose itself (detect-only).
    line = _exec_start_pre_lines(UI_UNIT.read_text())[0]
    # The remediation is quoted text for the operator, not an executed command:
    # the backstop's own action is only `test ... || echo ... exit 1`.
    assert "install-cli.sh" in line  # present as guidance...
    assert "bash scripts/systemd/install-cli.sh" not in line.split("echo", 1)[0]


# ───────────────────────── behavior of the check ───────────────────────


def _backstop_command(text: str) -> str:
    """Extract the `/bin/sh -c '<cmd>'` payload from the ExecStartPre line."""
    line = _exec_start_pre_lines(text)[0]
    prefix = "ExecStartPre=/bin/sh -c "
    assert line.startswith(prefix), line
    payload = line[len(prefix):]
    # systemd single-quotes the script; strip one surrounding pair.
    assert payload.startswith("'") and payload.endswith("'"), payload
    return payload[1:-1]


def _run_backstop(cmd: str, fake_root: Path) -> subprocess.CompletedProcess[str]:
    """Run the backstop command with /opt rerooted under a tmp dir.

    The command hardcodes absolute /opt paths; we rewrite them to the fake root
    so the test is hermetic and touches no real system path.
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
    ep = venv_bin / "llauncher-ui"
    ep.write_text("#!/bin/sh\n")
    ep.chmod(0o755)
    return ep


def test_backstop_noop_when_entrypoint_present(tmp_path: Path):
    """Present, executable entry point ⇒ clean exit, no message."""
    cmd = _backstop_command(UI_UNIT.read_text())
    _make_entrypoint(tmp_path)

    result = _run_backstop(cmd, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.strip() == ""


def test_backstop_fails_loud_when_venv_missing(tmp_path: Path):
    """No /opt venv at all ⇒ nonzero + remediation on stderr."""
    cmd = _backstop_command(UI_UNIT.read_text())

    result = _run_backstop(cmd, tmp_path)

    assert result.returncode != 0
    assert "install-cli.sh" in result.stderr


def test_backstop_fails_loud_when_entrypoint_missing(tmp_path: Path):
    """Venv dir exists but the entry point does not ⇒ fail loud."""
    cmd = _backstop_command(UI_UNIT.read_text())
    (tmp_path / "opt/llauncher/venv").mkdir(parents=True)

    result = _run_backstop(cmd, tmp_path)

    assert result.returncode != 0
    assert "install-cli.sh" in result.stderr


def test_backstop_fails_loud_when_entrypoint_not_executable(tmp_path: Path):
    """Entry point present but not executable ⇒ 'usable' check fails loud."""
    cmd = _backstop_command(UI_UNIT.read_text())
    ep = _make_entrypoint(tmp_path)
    ep.chmod(0o644)

    result = _run_backstop(cmd, tmp_path)

    assert result.returncode != 0
    assert "install-cli.sh" in result.stderr
