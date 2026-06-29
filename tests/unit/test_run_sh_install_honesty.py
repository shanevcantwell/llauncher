"""Regression guards for ``scripts/run.sh`` install honesty (issue #154).

Background
----------
``run.sh install`` used to ``pip install`` into the repo-local ``.venv``
while printing ``✓ Installation complete`` plus a list of ``./run.sh``
commands — implying a *global* readiness it never delivered. The console
scripts landed in ``.venv/bin`` and were invisible to the operator's
global shell (``llauncher-ui`` → ``command not found``).

Worse, the venv bootstrap ran *unconditionally* before the command
dispatch, so even the now-disabled ``install`` — and a bare help
invocation — would re-create the ~498 MB venv as a side effect.

Issue #154's acceptance criteria (both pinned here):

  1. ``run.sh install`` no longer implies a global readiness it didn't
     deliver — it is disabled and points at the real global path.
  2. The documented global launch path
     (``pip install --user -e ".[ui]"``) is discoverable from the
     script's own output / help.

This module drives the real ``scripts/run.sh`` in a hermetic temp
``PROJECT_DIR`` (so a stray bootstrap would land in ``tmp_path``, never
the repo) and asserts both criteria plus the no-side-effect-venv
guarantee for the non-runtime commands.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


GLOBAL_INSTALL_CMD = 'pip install --user -e ".[ui]"'


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

    ``run.sh`` derives ``PROJECT_DIR`` as the parent of its own
    ``scripts`` dir, so any accidental ``.venv`` bootstrap lands under
    ``tmp_path`` and is easy to assert on — the real repo is never
    touched.
    """
    src = _repo_root() / "scripts" / "run.sh"
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    dest = scripts_dir / "run.sh"
    shutil.copy2(src, dest)
    return dest


def _invoke(run_sh: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(run_sh), *args],
        capture_output=True,
        text=True,
        check=False,
    )


# ─────────── Criterion 1: install is honest, no global implication ──


def test_install_is_disabled_and_nonzero(run_sh: Path):
    """``run.sh install`` must fail loudly, not pretend success."""
    result = _invoke(run_sh, "install")

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "disabled" in combined.lower()
    # It must NOT claim completion / global readiness.
    assert "Installation complete" not in combined
    assert "complete" not in combined.lower()


def test_install_points_at_global_path(run_sh: Path):
    """The disabled-install message surfaces the real global command."""
    result = _invoke(run_sh, "install")

    combined = result.stdout + result.stderr
    assert GLOBAL_INSTALL_CMD in combined


def test_install_does_not_bootstrap_venv(run_sh: Path, tmp_path: Path):
    """The disabled install must not create the venv it disavows.

    Pre-#154 the bootstrap ran before dispatch, so ``install`` built a
    ~498 MB venv before printing its banner. The fix moves bootstrap
    into the runtime commands only.
    """
    _invoke(run_sh, "install")

    assert not (tmp_path / ".venv").exists()


# ─────────── Criterion 2: global path discoverable from help ────────


@pytest.mark.parametrize("args", [(), ("help",), ("bogus-cmd",)])
def test_help_surfaces_global_install_path(run_sh: Path, args: tuple[str, ...]):
    """A bare / unknown invocation prints the global install command."""
    result = _invoke(run_sh, *args)

    combined = result.stdout + result.stderr
    assert GLOBAL_INSTALL_CMD in combined


def test_help_does_not_route_setup_to_disabled_install(run_sh: Path):
    """Help must not send first-time users to the disabled ``install``.

    The old help block said ``First time setup:  ./run.sh install`` —
    a dead end now that install is disabled. The setup line must point
    at the global pip path instead.
    """
    result = _invoke(run_sh)

    combined = result.stdout + result.stderr
    # The "first time setup" guidance must not name the install subcommand.
    setup_lines = [
        line for line in combined.splitlines() if "first time setup" in line.lower()
    ]
    assert setup_lines, "help should still have a first-time-setup section"
    for line in setup_lines:
        assert "install" not in line.lower() or "pip install" in line.lower()


def test_help_does_not_bootstrap_venv(run_sh: Path, tmp_path: Path):
    """Printing help must have no venv side effect."""
    _invoke(run_sh)

    assert not (tmp_path / ".venv").exists()
