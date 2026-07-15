"""Unit tests for the Windows installer's legacy-key migration + dedupe.

Issue #285 (installer half of the "403s keep coming back" recurrence): the
PowerShell installer's pre-#139 migration must DROP a legacy line whose
migrated key already exists as a canonical line, mirroring the shell
installer exactly so both resolve duplicates identically. The logic is
extracted to ``scripts/windows/MigrateEnvKeys.ps1`` (function
``Invoke-EnvKeyMigration``) so it is testable without install.ps1's
ACL/NSSM steps.

Gated on ``pwsh`` (PowerShell Core) being on PATH. On a host without it —
including this CI/dev box — the test SKIPS rather than fakes a pass; the
field confirmation of the PowerShell path is a Windows-box user:gate
follow-up. When pwsh IS present (Windows CI, the operator's box) these run
and pin parity with the shell installer's behavior.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("pwsh") is None,
    reason="pwsh (PowerShell Core) not available; PowerShell dedupe is a "
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


def test_legacy_token_colliding_with_canonical_is_dropped() -> None:
    result = _invoke(
        [
            "LLAUNCHER_AGENT_TOKEN=good",
            "# comment",
            "LAUNCHER_AGENT_TOKEN=legacy-bad",
            "LAUNCHER_AGENT_HOST=1.2.3.4",
            "LLAUNCHER_AGENT_PORT=8765",
        ]
    )
    lines = _as_list(result["Lines"])
    token_lines = [ln for ln in lines if ln.startswith("LLAUNCHER_AGENT_TOKEN=")]
    assert token_lines == ["LLAUNCHER_AGENT_TOKEN=good"]
    assert "LAUNCHER_AGENT_TOKEN=legacy-bad" not in lines
    assert "LLAUNCHER_AGENT_HOST=1.2.3.4" in lines
    dropped = _as_list(result["Dropped"])
    assert any("LLAUNCHER_AGENT_TOKEN" in d for d in dropped)


def test_legacy_only_migrates_without_dropping() -> None:
    result = _invoke(
        ["LAUNCHER_AGENT_TOKEN=only-legacy", "LAUNCHER_AGENT_HOST=1.2.3.4"]
    )
    lines = _as_list(result["Lines"])
    assert lines.count("LLAUNCHER_AGENT_TOKEN=only-legacy") == 1
    assert "LLAUNCHER_AGENT_HOST=1.2.3.4" in lines
    assert _as_list(result["Dropped"]) == []
    assert len(_as_list(result["Migrated"])) == 2


def test_no_legacy_keys_is_noop() -> None:
    original = ["LLAUNCHER_AGENT_TOKEN=good", "LLAUNCHER_AGENT_HOST=9.9.9.9"]
    result = _invoke(original)
    assert _as_list(result["Lines"]) == original
    assert _as_list(result["Migrated"]) == []
    assert _as_list(result["Dropped"]) == []
