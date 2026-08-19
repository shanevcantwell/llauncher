"""Static-source + byte-inspection guard for issue #382.

``install.ps1`` used to seed the live ``agent.env`` by copying the
*entire* ``agent.env.example`` template verbatim -- including its
Unicode box-drawing comment banners (``── ... ──``). Windows
PowerShell 5.1's default codepage handling mangled that Unicode on
write, so a fresh install's live ``agent.env`` carried a UTF-8 BOM
(``EF BB BF``) plus mojibake bytes, even though the installer already
wrote via the BOM-free ``[System.IO.File]::WriteAllLines(...,
UTF8Encoding($false))`` pattern (issue #127) -- the corruption came from
the *source bytes* being copied, not the write encoding.

Fix: seed only the template's live (non-blank, non-``#``-comment)
``KEY=VALUE`` lines -- the same filter already used a few dozen lines
below to build NSSM's ``AppEnvironmentExtra`` from the live file. No
comment banner, and therefore no non-ASCII byte, ever reaches the write
call.

These are static-source (text) assertions plus a byte-level simulation
of the PowerShell filter against the real template, not a
``pwsh``-execution test -- same posture as
``tests/unit/test_install_ps1_no_bom_env_writes.py``.
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
ENV_EXAMPLE = _repo_root() / "scripts" / "windows" / "agent.env.example"


def _source() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


def _template_key_lines() -> list[str]:
    """Reimplementation (Python side) of the PowerShell filter added for
    #382: keep only non-blank, non-``#``-comment lines. Mirrors the
    existing NSSM ``$envPairs`` filter in install.ps1 exactly."""
    lines = []
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def test_seed_no_longer_copies_get_content_env_example_directly():
    """The two known seed call sites must no longer pass the raw
    ``(Get-Content $EnvExample)`` result straight into the write --
    that is the exact whole-template-copy shape issue #382 pins as the
    root cause of the BOM+mojibake corruption."""
    text = _source()
    offenders = [
        (lineno, raw)
        for lineno, raw in enumerate(text.splitlines(), start=1)
        if re.search(r"-replace\s+'replace-me-with-a-random-token'", raw)
        and "(Get-Content $EnvExample)" in raw
    ]
    assert not offenders, (
        "install.ps1 must not seed $EnvFile from the raw "
        "(Get-Content $EnvExample) result -- that copies the whole "
        "~5KB template including its comment banners (issue #382). "
        f"Offending line(s): {offenders}"
    )


def test_seed_filters_template_to_key_lines_before_writing():
    """A dedicated filtered-lines variable (comment/blank stripped) must
    exist and be the thing substituted and written, mirroring the
    existing $envPairs blank/#-comment filter used for AppEnvironmentExtra."""
    text = _source()
    assert re.search(
        r"\$templateKeyLines\s*=\s*@\(Get-Content \$EnvExample\)\s*\|\s*Where-Object",
        text,
    ), (
        "Expected a $templateKeyLines = @(Get-Content $EnvExample) | "
        "Where-Object {...} filter step building the minimal seed content."
    )
    # Both seed call sites (mirror-token path and fresh-token path) must
    # build $seedLines from the filtered variable, not the raw template.
    seed_lines_assignments = re.findall(
        r"\$seedLines\s*=\s*@\(\$templateKeyLines\b", text
    )
    assert len(seed_lines_assignments) >= 2, (
        "Expected both $EnvFile seed call sites (mirror-token carry-forward "
        "and fresh-token generation) to build $seedLines from "
        "$templateKeyLines, not the raw template. "
        f"Found {len(seed_lines_assignments)} matching assignment(s)."
    )


def test_filtered_template_lines_contain_only_required_keys():
    """Byte-inspection of the real template run through the same filter
    the installer now applies: the result must be exactly the
    uncommented KEY=VALUE lines (token/host/port/node-name) named in
    #382 -- no banner text, no box-drawing characters, nothing else."""
    key_lines = _template_key_lines()
    keys = [line.split("=", 1)[0] for line in key_lines]
    assert keys == [
        "LLAUNCHER_AGENT_TOKEN",
        "LLAUNCHER_AGENT_HOST",
        "LLAUNCHER_AGENT_PORT",
        "LLAUNCHER_AGENT_NODE_NAME",
    ], (
        "Filtering scripts/windows/agent.env.example down to its live "
        f"KEY=VALUE lines produced an unexpected key set: {keys}. If the "
        "template's required keys changed, update this pin; otherwise the "
        "filter regressed to letting extra content through."
    )


def test_filtered_template_lines_are_pure_ascii():
    """The minimal seed content the installer would write must be pure
    ASCII -- no box-drawing / smart-quote bytes survive the filter, so
    no encoding mismatch can reintroduce mojibake (issue #382's actual
    field symptom, observed at byte 62+ of a corrupted agent.env)."""
    for line in _template_key_lines():
        offenders = [ch for ch in line if ord(ch) > 0x7F]
        assert not offenders, (
            f"Filtered seed line {line!r} contains non-ASCII character(s) "
            f"{offenders!r} -- the minimal seed must be pure ASCII."
        )


def test_filtered_template_lines_have_no_bom():
    """Byte-level check: the template file itself carries no BOM, and the
    filtered lines' encoded bytes contain no stray EF BB BF sequence --
    guards the source side of the BOM defect (the write side is already
    pinned by test_install_ps1_no_bom_env_writes.py)."""
    raw_bytes = ENV_EXAMPLE.read_bytes()
    assert not raw_bytes.startswith(b"\xef\xbb\xbf"), (
        "scripts/windows/agent.env.example itself must not carry a UTF-8 BOM."
    )
    for line in _template_key_lines():
        assert b"\xef\xbb\xbf" not in line.encode("utf-8"), (
            f"Filtered seed line {line!r} encodes a BOM byte sequence."
        )
