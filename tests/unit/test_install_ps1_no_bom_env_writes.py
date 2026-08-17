"""Static-source guard for issue #127 (BOM in agent token).

``scripts/windows/install.ps1`` used to write ``$EnvFile`` at three call
sites using ``Set-Content -Encoding utf8`` (two seed-path writes inside the
``if (-not (Test-Path $EnvFile))`` block) and ``Add-Content -Encoding utf8``
(one mirror-migration append). Windows PowerShell 5.1 treats
``-Encoding utf8`` as UTF-8-**with**-BOM, so every one of those writes
prepended ``EF BB BF`` to ``agent.env`` -- the BOM then leaked into the
first parsed key/value and, via the token, into the ``X-Api-Key`` header,
crashing httpx's ASCII-only header encoder.

The fix replaces all three call sites with the BOM-free pattern already
used elsewhere in the same file:
``[System.IO.File]::WriteAllLines($EnvFile, <lines>, (New-Object
System.Text.UTF8Encoding($false)))``.

This is a static-source (text) assertion, not a ``pwsh``-execution test,
so it runs on any host without needing PowerShell installed -- same
posture as ``tests/architecture/test_ps1_ascii.py`` and
``tests/unit/test_install_ps1_unbuffered_env.py``.
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


def test_no_encoding_utf8_write_targets_env_file():
    """No live (non-comment) line may write $EnvFile via
    `-Encoding utf8` (Set-Content or Add-Content) -- that is the exact
    BOM-prepending call shape issue #127 pins as the root cause."""
    text = _source()
    offenders = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            continue
        if "EnvFile" not in raw_line:
            continue
        if re.search(r"-Encoding\s+utf8\b", raw_line, re.IGNORECASE):
            offenders.append(f"line {lineno}: {raw_line.strip()}")
    assert not offenders, (
        "install.ps1 must not write $EnvFile via -Encoding utf8 "
        "(Windows PowerShell 5.1 prepends a UTF-8 BOM, issue #127); "
        "use [System.IO.File]::WriteAllLines(...) with "
        "UTF8Encoding($false)) instead. Offending line(s):\n"
        + "\n".join(offenders)
    )


def test_env_file_seed_and_migration_writes_use_writealllines():
    """The three known $EnvFile write sites (two template seeds, one
    mirror-token migration append) must all go through
    [System.IO.File]::WriteAllLines with a BOM-free UTF8Encoding."""
    text = _source()
    matches = re.findall(
        r"\[System\.IO\.File\]::WriteAllLines\(\s*\$EnvFile\s*,\s*[^,]+,\s*"
        r"\(New-Object System\.Text\.UTF8Encoding\(\$false\)\)\s*\)",
        text,
        re.MULTILINE,
    )
    assert len(matches) >= 3, (
        "Expected at least 3 BOM-free [System.IO.File]::WriteAllLines($EnvFile, ...) "
        f"call sites in install.ps1, found {len(matches)}. These correspond to the "
        "two template-seed writes and the mirror-token migration append (issue #127)."
    )
