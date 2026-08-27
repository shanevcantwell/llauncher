"""Unit tests for the shared interpreter-floor guard (issue #334).

pyproject.toml declares ``requires-python = ">=3.11"``, but pre-#334 none of
the installers checked it before creating/using a venv — a <3.11 interpreter
installed silently and failed later at import time (trust-and-degrade
instead of fail-loud; PARSE-AT-THE-DOOR applied to prerequisites).

``check_python_floor`` is extracted to ``scripts/systemd/check_python_floor.sh``
(mirroring ``migrate_env_keys.sh`` / ``venv_manifest.sh``) so it is testable
in isolation, against REAL interpreters already on this host, without
driving the root-gated ``install-cli.sh`` or the systemctl-dependent
``install.sh``. Skipped if ``bash`` is unavailable.
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
HELPER = SYSTEMD_DIR / "check_python_floor.sh"
INSTALL_SH = SYSTEMD_DIR / "install.sh"
INSTALL_CLI = SYSTEMD_DIR / "install-cli.sh"


def _run_check(python_bin: str, major: str = "3", minor: str = "11") -> subprocess.CompletedProcess[str]:
    script = (
        "set -uo pipefail\n"
        f'source "{HELPER}"\n'
        f'check_python_floor "{python_bin}" "{major}" "{minor}"\n'
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def _make_fake_python(tmp_path: Path, name: str, version_info: str) -> Path:
    """A fake interpreter whose `-c '...sys.version_info...'` prints a fixed version."""
    fake = tmp_path / name
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "{version_info}"\n'
    )
    fake.chmod(0o755)
    return fake


# ─────────────────────── check_python_floor() ───────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "probes real interpreters by POSIX name (e.g. python3.11) and "
        "relies on POSIX exec bits for the stand-in interpreters; those "
        "binaries and that mechanism do not exist on Windows"
    ),
)
@pytest.mark.parametrize("py_bin", ["python3.11", "python3.12"])
def test_floor_passes_for_real_interpreters_at_or_above_floor(py_bin: str):
    """Against real interpreters already on this host (>=3.11), the check
    must be silent and exit 0."""
    if shutil.which(py_bin) is None:
        pytest.skip(f"{py_bin} not available on this host")

    result = _run_check(py_bin)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "probes real interpreters by POSIX name (e.g. python3.11) and "
        "relies on POSIX exec bits for the stand-in interpreters; those "
        "binaries and that mechanism do not exist on Windows"
    ),
)
def test_floor_fails_loud_for_real_interpreter_below_floor():
    """Against a real 3.10 interpreter (below the 3.11 floor), the check
    must exit nonzero and name BOTH the found version and the required
    floor."""
    if shutil.which("python3.10") is None:
        pytest.skip("python3.10 not available on this host")

    result = _run_check("python3.10")

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "3.10" in combined
    assert "3.11" in combined


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "probes real interpreters by POSIX name (e.g. python3.11) and "
        "relies on POSIX exec bits for the stand-in interpreters; those "
        "binaries and that mechanism do not exist on Windows"
    ),
)
def test_floor_fails_loud_when_interpreter_missing():
    """A nonexistent binary must fail loud, naming the binary — never
    silently proceed as if the floor were satisfied."""
    result = _run_check("python3.99-definitely-nonexistent")

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "not found" in combined
    assert "python3.99-definitely-nonexistent" in combined


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "probes real interpreters by POSIX name (e.g. python3.11) and "
        "relies on POSIX exec bits for the stand-in interpreters; those "
        "binaries and that mechanism do not exist on Windows"
    ),
)
def test_floor_boundary_exact_minor_passes(tmp_path: Path):
    """Exactly the floor version (3.11) must pass, not be rejected as
    below it (off-by-one guard on the comparison)."""
    fake = _make_fake_python(tmp_path, "fake-python", "3.11")
    script = (
        "set -uo pipefail\n"
        f'source "{HELPER}"\n'
        f'check_python_floor "{fake}" "3" "11"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_floor_boundary_one_minor_below_fails(tmp_path: Path):
    """3.10 (one minor below a 3.11 floor) must fail."""
    fake = _make_fake_python(tmp_path, "fake-python", "3.10")
    script = (
        "set -uo pipefail\n"
        f'source "{HELPER}"\n'
        f'check_python_floor "{fake}" "3" "11"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "probes real interpreters by POSIX name (e.g. python3.11) and "
        "relies on POSIX exec bits for the stand-in interpreters; those "
        "binaries and that mechanism do not exist on Windows"
    ),
)
def test_floor_major_version_above_floor_passes(tmp_path: Path):
    """A hypothetical future major version (4.0) must clear a 3.11 floor."""
    fake = _make_fake_python(tmp_path, "fake-python", "4.0")
    script = (
        "set -uo pipefail\n"
        f'source "{HELPER}"\n'
        f'check_python_floor "{fake}" "3" "11"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "probes real interpreters by POSIX name (e.g. python3.11) and "
        "relies on POSIX exec bits for the stand-in interpreters; those "
        "binaries and that mechanism do not exist on Windows"
    ),
)
def test_floor_uses_caller_err_when_defined(tmp_path: Path):
    """When the caller defines an `err` function (as install.sh does), the
    helper's messages route through it rather than bare stderr."""
    script = (
        "set -uo pipefail\n"
        'err() { echo "ERRHOOK: $1"; }\n'
        f'source "{HELPER}"\n'
        'check_python_floor "python3.99-definitely-nonexistent" "3" "11"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "ERRHOOK:" in result.stdout


# ─────────────────────── static wiring ───────────────────────


def test_install_sh_sources_the_floor_helper():
    text = INSTALL_SH.read_text()
    assert '. "$SCRIPT_DIR/check_python_floor.sh"' in text


def test_install_sh_calls_the_floor_check_before_uninstall_dispatch_returns():
    """The floor check must run for every non-uninstall invocation, ahead of
    the venv-existence preflight it would otherwise let slip past silently."""
    lines = INSTALL_SH.read_text().splitlines()
    check_idx = next(
        i for i, ln in enumerate(lines) if "check_python_floor python3 3 11" in ln
    )
    preflight_idx = next(
        i for i, ln in enumerate(lines) if "--- Preflight ---" in ln
    )
    assert check_idx < preflight_idx


def test_install_cli_sh_sources_the_floor_helper():
    text = INSTALL_CLI.read_text()
    assert '. "$SCRIPT_DIR/check_python_floor.sh"' in text


def test_install_cli_sh_calls_the_floor_check_before_venv_creation():
    """The floor check must run BEFORE `python3 -m venv`, never after —
    otherwise a <3.11 interpreter would already have built the venv by the
    time the check fired."""
    lines = INSTALL_CLI.read_text().splitlines()
    check_idx = next(
        i for i, ln in enumerate(lines) if "check_python_floor python3 3 11" in ln
    )
    venv_idx = next(
        i for i, ln in enumerate(lines) if ln.strip().startswith("[ -x ") and "python3 -m venv" in ln
    )
    assert check_idx < venv_idx
