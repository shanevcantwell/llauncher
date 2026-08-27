"""Guards for the pinned-venv compose ritual's manifest (#357 ratified
Option A, issue #360).

The compose ritual (``scripts/systemd/install-cli.sh``) records exactly
what it installed to ``/opt/llauncher/venv-manifest.txt`` — "the pin,
answerable at any time" per the issue's acceptance criteria — via
``write_venv_manifest`` (extracted to ``scripts/systemd/venv_manifest.sh``
so it is testable without driving the real network install). These tests
never touch the real ``/opt/llauncher/venv`` or the network: they call
``write_venv_manifest`` directly against a fake ``pip`` and a ``tmp_path``
manifest destination.

A second group asserts install-cli.sh's static wiring: ``llauncher-agent``
is now a symlinked script (the gap #360 closes — it was previously absent
from ``SCRIPTS``, so the agent's --user unit had no ``/usr/local/bin``
symlink to resolve through), and the manifest path/sourcing are wired
in-script.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
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
INSTALL_CLI = SYSTEMD_DIR / "install-cli.sh"
MANIFEST_HELPER = SYSTEMD_DIR / "venv_manifest.sh"


# ───────────────────────── write_venv_manifest() ────────────────────────


def _make_fake_pip(tmp_path: Path, freeze_output: str) -> Path:
    """A fake `pip` whose `freeze` subcommand prints fixed output."""
    fake_pip = tmp_path / "fake-pip"
    fake_pip.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "freeze" ]; then\n'
        f'  printf %s "{freeze_output}"\n'
        "fi\n"
    )
    fake_pip.chmod(0o755)
    return fake_pip


def _write_manifest(
    pip_bin: Path, manifest_path: Path, ref: str
) -> subprocess.CompletedProcess[str]:
    script = (
        "set -euo pipefail\n"
        f'source "{MANIFEST_HELPER}"\n'
        f'write_venv_manifest "{pip_bin}" "{manifest_path}" "{ref}"\n'
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "composes the venv manifest by running pip freeze inside a "
        "rerooted POSIX venv layout (bin/, exec bits); that layout does "
        "not exist on Windows"
    ),
)
def test_manifest_contains_ref_and_pip_freeze(tmp_path: Path):
    manifest = tmp_path / "venv-manifest.txt"
    fake_pip = _make_fake_pip(tmp_path, "llauncher==0.4.0\nfastapi==0.111.0\n")

    _write_manifest(fake_pip, manifest, "v0.4.0-alpha")

    text = manifest.read_text()
    assert "# ref: v0.4.0-alpha" in text
    assert "llauncher==0.4.0" in text
    assert "fastapi==0.111.0" in text


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "composes the venv manifest by running pip freeze inside a "
        "rerooted POSIX venv layout (bin/, exec bits); that layout does "
        "not exist on Windows"
    ),
)
def test_manifest_has_a_composed_timestamp(tmp_path: Path):
    manifest = tmp_path / "venv-manifest.txt"
    fake_pip = _make_fake_pip(tmp_path, "llauncher==0.4.0\n")

    _write_manifest(fake_pip, manifest, "main")

    text = manifest.read_text()
    composed_lines = [ln for ln in text.splitlines() if ln.startswith("# composed:")]
    assert len(composed_lines) == 1, composed_lines
    # UTC ISO-8601-ish stamp, e.g. "# composed: 2026-07-17T12:00:00Z"
    assert composed_lines[0].endswith("Z")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "composes the venv manifest by running pip freeze inside a "
        "rerooted POSIX venv layout (bin/, exec bits); that layout does "
        "not exist on Windows"
    ),
)
def test_manifest_warns_against_hand_editing(tmp_path: Path):
    manifest = tmp_path / "venv-manifest.txt"
    fake_pip = _make_fake_pip(tmp_path, "llauncher==0.4.0\n")

    _write_manifest(fake_pip, manifest, "main")

    assert "do not hand-edit" in manifest.read_text().lower()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "composes the venv manifest by running pip freeze inside a "
        "rerooted POSIX venv layout (bin/, exec bits); that layout does "
        "not exist on Windows"
    ),
)
def test_recompose_overwrites_stale_manifest(tmp_path: Path):
    """Re-running the ritual must describe the CURRENT venv, not append to
    or preserve a stale prior composition."""
    manifest = tmp_path / "venv-manifest.txt"
    old_pip = _make_fake_pip(tmp_path, "llauncher==0.3.0\n")
    _write_manifest(old_pip, manifest, "v0.3.0")
    assert "llauncher==0.3.0" in manifest.read_text()

    new_pip = _make_fake_pip(tmp_path, "llauncher==0.4.0\n")
    _write_manifest(new_pip, manifest, "v0.4.0-alpha")

    text = manifest.read_text()
    assert "llauncher==0.4.0" in text
    assert "llauncher==0.3.0" not in text
    assert "# ref: v0.4.0-alpha" in text
    assert "# ref: v0.3.0" not in text


# ───────────────────────── install-cli.sh static wiring ────────────────


def test_llauncher_agent_is_symlinked_by_install_cli():
    """The gap #360 closes: llauncher-agent was previously absent from
    SCRIPTS, so the agent --user unit had no /usr/local/bin symlink to
    resolve ExecStart through. It must be a first-class symlinked script,
    alongside the CLI/UI scripts install-cli.sh already placed."""
    text = INSTALL_CLI.read_text()
    assert "SCRIPTS=(llauncher llauncher-agent llauncher-mcp llauncher-ui)" in text


def test_install_cli_sources_the_manifest_helper():
    text = INSTALL_CLI.read_text()
    assert '. "$SCRIPT_DIR/venv_manifest.sh"' in text


def test_install_cli_writes_manifest_before_symlinking():
    """The manifest must describe the composition BEFORE the console
    scripts are (re)pointed at it, so a partial/failed manifest write never
    leaves symlinks pointing at an undocumented pin."""
    lines = INSTALL_CLI.read_text().splitlines()
    manifest_idx = next(
        i for i, ln in enumerate(lines) if "write_venv_manifest" in ln
    )
    symlink_idx = next(
        i for i, ln in enumerate(lines) if 'ln -sfn "$VENV/bin/$s"' in ln
    )
    assert manifest_idx < symlink_idx


def test_manifest_path_is_under_prefix():
    text = INSTALL_CLI.read_text()
    assert 'MANIFEST="$PREFIX/venv-manifest.txt"' in text


def test_uninstall_removes_the_manifest_with_the_prefix():
    """--uninstall's rm -rf "$PREFIX" already covers the manifest (it lives
    under $PREFIX) — pin this so a future refactor that moves the manifest
    out from under $PREFIX is caught."""
    text = INSTALL_CLI.read_text()
    assert 'rm -rf "$PREFIX"' in text
