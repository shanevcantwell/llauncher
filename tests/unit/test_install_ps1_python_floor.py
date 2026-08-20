"""Static-source guard for issue #334 (Windows installer interpreter floor).

``pyproject.toml`` declares ``requires-python = ">=3.11"``, but
``scripts/windows/install.ps1`` only checked that the venv's
``python.exe`` EXISTED (``Test-Path $VenvExe``) -- never that it cleared the
floor. A <3.11 venv installed the service silently and failed later at
import time (trust-and-degrade instead of fail-loud; PARSE-AT-THE-DOOR
applied to prerequisites). The fix queries the venv's own ``python.exe`` for
its ``sys.version_info`` and dies loud, naming both the found version and
the required floor, before the service is wired to it.

These are static-source (text) assertions, not a ``pwsh``-execution test, so
they run on any host without needing PowerShell installed -- same posture as
``tests/architecture/test_ps1_ascii.py`` and
``tests/unit/test_install_ps1_unbuffered_env.py``. A live-``pwsh`` parity
check is out of scope here per the issue's own posture ("PS1
static/parity"); this file supplies the static half.
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


INSTALL_PS1 = _repo_root() / "scripts" / "windows" / "install.ps1"


def _source() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


def test_floor_check_queries_venv_python_version_info():
    """The interpreter floor must be read from the venv's OWN python.exe
    ($VenvPython), not assumed or read from the invoking shell's python."""
    text = _source()
    assert "$VenvPython" in text
    assert "sys.version_info" in text


def test_floor_check_names_the_311_floor():
    """The floor (3.11, from pyproject.toml requires-python) must appear
    literally so a drifted floor is caught by a diff, not silently stale."""
    text = _source()
    assert "$RequiredMajor = 3" in text
    assert "$RequiredMinor = 11" in text


def test_floor_check_dies_on_a_below_floor_interpreter():
    """A found version below the floor must route through Die (nonzero
    exit), never a silent continue."""
    text = _source()
    match = re.search(
        r"if\s*\(\$foundMajor\s*-lt\s*\$RequiredMajor.*?\)\s*\{",
        text,
        re.DOTALL,
    )
    assert match is not None, "below-floor comparison not found"
    # The block immediately following the comparison must call Die.
    tail = text[match.end() : match.end() + 400]
    assert "Die" in tail


def test_floor_check_precedes_venv_exe_wiring():
    """The floor check must run before the service is configured to use
    $VenvExe -- catching a bad interpreter before it's wired in, not after."""
    lines = _source().splitlines()
    floor_idx = next(
        i for i, ln in enumerate(lines) if "$RequiredMajor = 3" in ln
    )
    nssm_install_idx = next(
        i
        for i, ln in enumerate(lines)
        if "nssm install $ServiceName $VenvExe" in ln
    )
    assert floor_idx < nssm_install_idx


def test_floor_check_precedes_env_file_seeding():
    """The floor check must run before any env-file / ACL side effects --
    a below-floor host should fail before touching agent.env at all."""
    lines = _source().splitlines()
    floor_idx = next(
        i for i, ln in enumerate(lines) if "$RequiredMajor = 3" in ln
    )
    env_dir_idx = next(
        i for i, ln in enumerate(lines) if "New-Item -ItemType Directory -Path $EnvDir" in ln
    )
    assert floor_idx < env_dir_idx


def test_floor_check_reports_lastexitcode_failure():
    """A failed version query (e.g. a broken venv python.exe) must itself be
    fail-loud, not silently treated as floor-satisfying."""
    text = _source()
    assert "$LASTEXITCODE -ne 0" in text
