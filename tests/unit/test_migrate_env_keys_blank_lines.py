"""Regression test: ``Invoke-EnvKeyMigration`` must accept blank-line
(empty-string) elements in ``-Lines`` without a parameter-binding error.

Issue #303: ``agent.env`` ships with blank lines (the template
``agent.env.example`` ships 10). ``install.ps1`` calls
``Invoke-EnvKeyMigration -Lines @(Get-Content $EnvFile)``, so ``-Lines`` is a
``string[]`` containing empty-string elements. A ``[Parameter(Mandatory)]``
``[string[]]`` rejects empty-string elements unless ``[AllowEmptyString()]``
is also present -- ``[AllowEmptyCollection()]`` alone only permits an empty
*array*, not empty *elements* -- so the installer aborted with "Cannot bind
argument to parameter 'Lines' because it is an empty string." on the first
blank line.

Gated on ``pwsh`` (PowerShell Core) being on PATH, mirroring
``tests/unit/test_install_ps1_dedupe.py`` exactly. On a host without it --
including this CI/dev box -- the test SKIPS rather than fakes a pass; true
verification is the Windows CI runner (#304) plus the operator field-confirm
(#299).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("pwsh") is None,
    reason="pwsh (PowerShell Core) not available; PowerShell migration is a "
    "Windows-box user:gate follow-up",
)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(f"Could not locate repo root from {here}")


def _invoke(lines: list[str]) -> dict:
    """Dot-source the module, run Invoke-EnvKeyMigration, return its result
    as a dict (Lines/Migrated/Dropped) via JSON round-trip."""
    module = _repo_root() / "scripts" / "windows" / "MigrateEnvKeys.ps1"
    ps_lines = "@(" + ",".join(f"'{ln}'" for ln in lines) + ")"
    script = (
        f". '{module}';\n"
        f"$r = Invoke-EnvKeyMigration -Lines {ps_lines};\n"
        # Force arrays so single-element results still serialize as arrays.
        "$out = [PSCustomObject]@{"
        "  Lines = @($r.Lines);"
        "  Migrated = @($r.Migrated);"
        "  Dropped = @($r.Dropped);"
        "};\n"
        "$out | ConvertTo-Json -Compress -Depth 4\n"
    )
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def test_blank_lines_do_not_raise_binding_error() -> None:
    """The core #303 regression: blank-line elements in -Lines must not
    trigger a Mandatory-parameter binding error."""
    # _invoke uses check=True: a non-zero pwsh exit (e.g. the parameter
    # binding error from #303) raises CalledProcessError, failing the test.
    result = _invoke(["", "LLAUNCHER_AGENT_TOKEN=good", ""])
    assert result is not None


def test_blank_lines_are_preserved_in_output() -> None:
    """Blank lines must pass THROUGH untouched -- filtering them at the
    call site would strip them from the rewritten agent.env, changing its
    formatting (explicitly disallowed by #303)."""
    result = _invoke(
        [
            "",
            "# a comment",
            "",
            "LLAUNCHER_AGENT_TOKEN=good",
            "",
        ]
    )
    lines = _as_list(result["Lines"])
    assert lines == [
        "",
        "# a comment",
        "",
        "LLAUNCHER_AGENT_TOKEN=good",
        "",
    ]


def test_blank_lines_with_legacy_key_migration_and_dedupe() -> None:
    """Full #303 scenario: blank lines + comments + a legacy key + a
    colliding canonical key, all in one input -- (a) no binding error,
    (b) blank lines preserved, (c) legacy key migrated, (d) duplicate
    collision dropped with canonical winning (#285)."""
    result = _invoke(
        [
            "",
            "# agent.env",
            "LLAUNCHER_AGENT_TOKEN=canonical-good",
            "",
            "LAUNCHER_AGENT_TOKEN=legacy-bad",
            "LAUNCHER_AGENT_HOST=1.2.3.4",
            "",
        ]
    )
    lines = _as_list(result["Lines"])

    # (a) no binding error -- implicit: _invoke would have raised.
    # (b) blank lines preserved (positions 0, 3, and trailing 6 in input
    # map to two remaining blanks once the dropped legacy line is removed).
    assert lines.count("") == 2
    assert "# agent.env" in lines

    # (c) legacy key migrated (LAUNCHER_AGENT_HOST has no canonical
    # collision, so it migrates rather than drops).
    assert "LLAUNCHER_AGENT_HOST=1.2.3.4" in lines
    assert "LAUNCHER_AGENT_HOST=1.2.3.4" not in lines

    # (d) duplicate collision dropped -- canonical wins, legacy line gone,
    # no second LLAUNCHER_AGENT_TOKEN line introduced.
    token_lines = [ln for ln in lines if ln.startswith("LLAUNCHER_AGENT_TOKEN=")]
    assert token_lines == ["LLAUNCHER_AGENT_TOKEN=canonical-good"]
    assert "LAUNCHER_AGENT_TOKEN=legacy-bad" not in lines

    dropped = _as_list(result["Dropped"])
    assert any("LLAUNCHER_AGENT_TOKEN" in d for d in dropped)

    migrated = _as_list(result["Migrated"])
    assert any("LAUNCHER_AGENT_HOST" in m for m in migrated)
