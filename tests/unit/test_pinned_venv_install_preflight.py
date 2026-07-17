"""Guards for the installers' pinned-venv preflight (#357 ratified Option A,
issue #360).

Both ``scripts/systemd/install.sh`` (--user mode) and
``scripts/systemd/install-ui.sh`` must fail loud, at install time, when
``/opt/llauncher/venv`` has never been composed on the host — with **no
silent fallback to a repo venv** (the acceptance criterion's own words).
``install.sh --system`` mode is UNCHANGED by #360 (it still recomposes this
checkout's dev-tree ``.venv`` via ADR-023 Phase A) and is asserted to stay
that way.

These tests never touch the real ``/opt/llauncher/venv`` (a fully composed
one may already exist on the dev host — out of this issue's scope to
compose or depend on it either way). Each copies the real installer script
into a hermetic ``tmp_path`` and rewrites its hardcoded ``/opt/llauncher/venv``
occurrences to a fake root under ``tmp_path`` before running it — the same
rerooting technique ``test_ui_venv_backstop.py`` uses for the unit template's
``ExecStartPre`` command, applied here to the whole installer script.

Both installers exit during preflight (well before any ``systemctl`` call)
when the pinned venv is absent, so these tests need no live systemd user
session — the process under test never reaches that far.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not available"
)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


SYSTEMD_DIR = _repo_root() / "scripts" / "systemd"


def _hermetic_systemd_copy(tmp_path: Path, fake_opt_venv: Path) -> Path:
    """Copy scripts/systemd/ into tmp_path with /opt/llauncher/venv rerooted.

    SCRIPT_DIR is derived from BASH_SOURCE at runtime, so sibling files
    (templates, migrate_env_keys.sh) must be copied alongside for a faithful
    hermetic run. The hardcoded ``/opt/llauncher/venv`` string is rewritten
    to ``fake_opt_venv`` in every copied file so the preflight check never
    touches the real system path.
    """
    dest_dir = tmp_path / "scripts" / "systemd"
    shutil.copytree(SYSTEMD_DIR, dest_dir)
    for f in dest_dir.iterdir():
        if f.is_file():
            text = f.read_text()
            if "/opt/llauncher/venv" in text:
                f.write_text(text.replace("/opt/llauncher/venv", str(fake_opt_venv)))
    return dest_dir


def _make_entrypoint(fake_opt_venv: Path, name: str) -> Path:
    venv_bin = fake_opt_venv / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    ep = venv_bin / name
    ep.write_text("#!/bin/sh\n")
    ep.chmod(0o755)
    return ep


# ─────────────────────── install.sh (--user mode) ───────────────────────


def test_user_mode_fails_loud_when_pinned_venv_absent(tmp_path: Path):
    """--user install.sh must exit nonzero, naming the ritual, when the
    pinned /opt/llauncher/venv has never been composed."""
    fake_opt_venv = tmp_path / "opt" / "llauncher" / "venv"
    systemd_dir = _hermetic_systemd_copy(tmp_path, fake_opt_venv)
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}

    result = subprocess.run(
        ["bash", str(systemd_dir / "install.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "opt/llauncher/venv" in combined or str(fake_opt_venv) in combined
    assert "install-cli.sh" in combined
    assert "run-as-a-service.md" in combined
    assert "Composing the pinned runtime venv" in combined
    # No silent fallback message implying success:
    assert "Rendered unit" not in combined


def test_user_mode_preflight_passes_when_pinned_venv_present(tmp_path: Path):
    """With the pinned entry point present, preflight itself must not be the
    reason a run stops — it must get past the venv check. (The script may
    still stop later at a real systemctl call inside this hermetic sandbox;
    we only assert the preflight boundary, per the acceptance criterion's
    'installer preflight logic' scope.)"""
    fake_opt_venv = tmp_path / "opt" / "llauncher" / "venv"
    systemd_dir = _hermetic_systemd_copy(tmp_path, fake_opt_venv)
    _make_entrypoint(fake_opt_venv, "llauncher-agent")
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}

    result = subprocess.run(
        ["bash", str(systemd_dir / "install.sh"), "--no-start"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    combined = result.stdout + result.stderr
    # The pinned-venv preflight's own failure message must NOT appear —
    # whatever happens next (real systemctl unavailable in this sandbox is
    # an acceptable, different failure), it isn't this preflight rejecting us.
    assert "There is no" not in combined or "fallback to a" not in combined
    assert "not been composed" not in combined


def test_system_mode_preflight_unchanged_checks_dev_tree_venv(tmp_path: Path):
    """--system mode must NOT be redirected to /opt — it still checks this
    checkout's own .venv (ADR-023 Phase A, untouched by #360). Run as a
    non-root synthetic invocation to exercise only the venv preflight
    ordering (it fails before the root check would even matter, since the
    venv preflight runs first)."""
    fake_opt_venv = tmp_path / "opt" / "llauncher" / "venv"
    systemd_dir = _hermetic_systemd_copy(tmp_path, fake_opt_venv)
    project_dir = systemd_dir.parent.parent
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}

    result = subprocess.run(
        ["bash", str(systemd_dir / "install.sh"), "--system"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    combined = result.stdout + result.stderr
    # It must be complaining about the DEV TREE venv, never /opt.
    assert str(project_dir / ".venv" / "bin" / "llauncher-agent") in combined
    assert "run.sh setup" in combined
    assert str(fake_opt_venv) not in combined


# ─────────────────────── install-ui.sh ───────────────────────


def test_install_ui_fails_loud_when_pinned_venv_absent(tmp_path: Path):
    """install-ui.sh must exit nonzero, naming the ritual, when the pinned
    /opt/llauncher/venv has never been composed — this is a HARD preflight
    (issue #360), unlike the softer symlink/group warnings beside it."""
    fake_opt_venv = tmp_path / "opt" / "llauncher" / "venv"
    systemd_dir = _hermetic_systemd_copy(tmp_path, fake_opt_venv)
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}

    result = subprocess.run(
        ["bash", str(systemd_dir / "install-ui.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "not been composed" in combined
    assert "install-cli.sh" in combined
    assert "run-as-a-service.md" in combined
    assert "Composing the pinned runtime venv" in combined
    assert "Rendered unit" not in combined


def test_install_ui_preflight_passes_when_pinned_venv_present(tmp_path: Path):
    """With the pinned llauncher-ui entry point present, the hard preflight
    must not be what stops the run."""
    fake_opt_venv = tmp_path / "opt" / "llauncher" / "venv"
    systemd_dir = _hermetic_systemd_copy(tmp_path, fake_opt_venv)
    _make_entrypoint(fake_opt_venv, "llauncher-ui")
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}

    result = subprocess.run(
        ["bash", str(systemd_dir / "install-ui.sh")],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    combined = result.stdout + result.stderr
    assert "not been composed" not in combined
