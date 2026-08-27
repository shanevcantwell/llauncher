"""Regression guard for ``scripts/run.sh stop`` (issue #229).

Background
----------
The ``stop`` case called ``ensure_venv`` before ``llauncher-agent --stop``.
On a machine that installed globally via ``pip install --user -e .``
(no local ``.venv``), running ``./run.sh stop`` silently bootstrapped a full
~500MB venv as a side effect of *stopping* the agent — contradicting the
no-side-effect / install-honesty goal issue #219 delivered (see also #154,
guarded by ``test_run_sh_install_honesty.py``).

The fix: ``stop`` no longer calls ``ensure_venv`` (which *creates* the venv
when absent). Instead it activates the repo-local ``.venv`` only if one
already exists, and otherwise relies on PATH (a global install puts
``llauncher-agent`` on PATH via ``~/.local/bin``).

This module drives the real ``scripts/run.sh`` in a hermetic temp
``PROJECT_DIR`` (mirroring the fixture style of
``test_run_sh_install_honesty.py``), with a stub ``llauncher-agent`` on
PATH standing in for the real console script.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "drives the real scripts/run.sh stop as a bash script; run.sh's "
        "shebang, PATH-shadowed console-script stand-in, and POSIX venv "
        "bin/ layout don't exist on Windows"
    ),
)


def _repo_root() -> Path:
    """Walk up from this file until a ``.git`` entry is found."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


@pytest.fixture
def run_sh(tmp_path: Path) -> Path:
    """Copy the real ``run.sh`` into a hermetic ``PROJECT_DIR/scripts``.

    ``run.sh`` derives ``PROJECT_DIR`` as the parent of its own ``scripts``
    dir, so any accidental ``.venv`` bootstrap lands under ``tmp_path`` and
    is easy to assert on — the real repo is never touched.
    """
    src = _repo_root() / "scripts" / "run.sh"
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    dest = scripts_dir / "run.sh"
    shutil.copy2(src, dest)
    return dest


def _make_stub_agent_on_path(tmp_path: Path) -> dict[str, str]:
    """Put a stub ``llauncher-agent`` on PATH, simulating a global install."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    stub = bin_dir / "llauncher-agent"
    stub.write_text('#!/bin/bash\necho "stub llauncher-agent called: $*"\n')
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    # Ensure no ambient VIRTUAL_ENV leaks in from the invoking shell.
    env.pop("VIRTUAL_ENV", None)
    return env


def _invoke_stop(run_sh: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(run_sh), "stop"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_stop_does_not_bootstrap_venv_when_absent(run_sh: Path, tmp_path: Path):
    """No local .venv + global install on PATH ⇒ stop must not create one."""
    env = _make_stub_agent_on_path(tmp_path)

    result = _invoke_stop(run_sh, env)

    assert not (tmp_path / ".venv").exists()
    combined = result.stdout + result.stderr
    assert "Virtual environment not found" not in combined


def test_stop_still_invokes_agent_stop_without_venv(run_sh: Path, tmp_path: Path):
    """Even without a .venv, `stop` must still reach the agent via PATH."""
    env = _make_stub_agent_on_path(tmp_path)

    result = _invoke_stop(run_sh, env)

    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "stub llauncher-agent called: --stop" in combined


def test_stop_activates_existing_venv_if_present(run_sh: Path, tmp_path: Path):
    """A pre-existing .venv is still activated (its agent takes precedence)."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "activate").write_text(f'export PATH="{venv_bin}:$PATH"\n')
    venv_agent = venv_bin / "llauncher-agent"
    venv_agent.write_text('#!/bin/bash\necho "venv llauncher-agent called: $*"\n')
    venv_agent.chmod(0o755)

    # A different stub further down PATH must NOT be the one invoked, since
    # the venv's bin is prepended by `activate`.
    env = _make_stub_agent_on_path(tmp_path)

    result = _invoke_stop(run_sh, env)

    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "venv llauncher-agent called: --stop" in combined
    # The venv must not have been (re)created — only activated.
    assert "Virtual environment not found" not in combined
