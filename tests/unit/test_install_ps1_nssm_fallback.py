"""Static-source guard for issue #352 (nssm PATH fallback chain).

A Windows Update can silently drop ``C:\\ProgramData\\chocolatey\\bin`` from
the system PATH without touching the underlying choco/nssm install --
``nssm.exe`` keeps working fine from a shell that still has the entry, but
``Get-Command nssm.exe`` in a fresh/other shell comes back empty. The
pre-#352 installer treated that as "nssm not installed" and sent operators
to reinstall tooling that was never missing (the bogus precondition behind
PR #345, which risked clobbering the live service).

``scripts/windows/install.ps1`` must resolve nssm through a fallback chain,
first match wins, before ``Die``-ing:

  1. ``$env:NSSM`` override
  2. ``nssm.exe`` on PATH (``Get-Command``)
  3. the choco bin shim: ``C:\\ProgramData\\chocolatey\\bin\\nssm.exe``
  4. the choco lib payload: ``C:\\ProgramData\\chocolatey\\lib\\nssm\\tools\\**\\nssm.exe``
  5. the scoop shim: ``%USERPROFILE%\\scoop\\shims\\nssm.exe``

and the single resolved ``$nssm`` variable must be the only thing every
NSSM invocation in the script uses -- no bare ``nssm`` calls scattered
through the file that would bypass the resolution chain.

These are static-source (text) assertions, not a ``pwsh``-execution test,
so they run on any host without needing PowerShell installed -- same
posture as ``test_install_ps1_unbuffered_env.py`` and
``tests/architecture/test_ps1_ascii.py``.
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


def test_env_nssm_override_checked():
    text = _source()
    assert "$env:NSSM" in text, (
        "install.ps1 must still honor an $env:NSSM override as the first "
        "link in the resolution chain."
    )


def test_env_nssm_override_invalid_path_warns_loudly():
    """A set-but-invalid $env:NSSM must be announced, not silently dropped
    (#352 review): an operator who set the override expected it to be used,
    and falling through without a word leaves them wondering why it was
    ignored."""
    text = _source()
    override_block = re.search(
        r"if \(\$env:NSSM\) \{(.*?)\n\}", text, re.DOTALL
    )
    assert override_block is not None, "Could not find the $env:NSSM override block."
    body = override_block.group(1)
    assert "else" in body and "Warn" in body, (
        "The $env:NSSM override block must emit a warning (Warn) in its "
        "else branch when Test-Path fails on the override, before falling "
        "through to the next candidate."
    )


def test_path_lookup_via_get_command():
    text = _source()
    assert re.search(r"Get-Command\s+nssm\.exe", text), (
        "install.ps1 must probe PATH via Get-Command nssm.exe."
    )


def test_choco_bin_shim_path_present():
    text = _source()
    assert r"C:\ProgramData\chocolatey\bin\nssm.exe" in text, (
        "install.ps1 must fall back to the chocolatey bin shim "
        r"C:\ProgramData\chocolatey\bin\nssm.exe (issue #352)."
    )


def test_choco_lib_payload_path_present():
    text = _source()
    assert r"C:\ProgramData\chocolatey\lib\nssm\tools" in text, (
        "install.ps1 must fall back to nssm.exe under the chocolatey lib "
        r"payload C:\ProgramData\chocolatey\lib\nssm\tools\**\nssm.exe "
        "(issue #352)."
    )


def test_scoop_shim_path_present():
    text = _source()
    assert "scoop\\shims\\nssm.exe" in text, (
        "install.ps1 must fall back to the scoop shim "
        r"%USERPROFILE%\scoop\shims\nssm.exe (issue #352)."
    )


def test_failure_message_names_every_probed_path():
    """On total failure, Die must enumerate every candidate tried, not just
    say 'not found' (operators need to know what was actually checked)."""
    text = _source()
    die_match = re.search(r"if \(-not \$nssm.*?Die @\"(.*?)\"@", text, re.DOTALL)
    assert die_match is not None, "Could not find the nssm-not-found Die block."
    die_body = die_match.group(1)
    assert "$tried" in die_body or "nssmCandidates" in text, (
        "The Die message must be built from the list of probed candidates."
    )


def test_no_bare_nssm_invocations_outside_resolution_block():
    """Every NSSM invocation after resolution must go through the resolved
    ``$nssm`` variable (``& $nssm ...``) -- a bare ``nssm`` call would
    silently bypass the fallback chain and reintroduce PATH-only failures."""
    text = _source()
    lines = text.splitlines()
    bare_invocations = []
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Match a command invocation of nssm that is NOT `& $nssm` and NOT
        # inside the resolution/documentation block itself (which legitimately
        # mentions nssm.exe / "nssm install" etc. in prose or as the PATH probe).
        if re.search(r"(?<!\$)\bnssm(?:\.exe)?\s+(install|remove|stop|start|set|get)\b", stripped, re.IGNORECASE):
            # Exclude prose/comment-style mentions like "choco install nssm"
            # or "nssm install is never called" (documentation, not a call).
            if re.match(r"^(choco|scoop)\s+install\s+nssm", stripped, re.IGNORECASE):
                continue
            if "`" in stripped or stripped.startswith("Registering") or "is never called" in stripped:
                continue
            bare_invocations.append((i, stripped))
    assert not bare_invocations, (
        "Found nssm invocation(s) not going through the resolved $nssm "
        f"variable: {bare_invocations}"
    )


def test_all_ampersand_nssm_calls_use_resolved_variable():
    """Every `&`-invoked nssm call in the script must use `$nssm`, the
    single variable populated by the fallback chain."""
    text = _source()
    amp_calls = re.findall(r"&\s*\$?\w[\w.:\\]*\s+(?:install|remove|stop|start|set)\s+\$ServiceName", text)
    # Every such call found via `& <something>` must specifically be `& $nssm`
    stray = re.findall(r"&\s+(?!\$nssm\b)(\S*nssm\S*)\s", text, re.IGNORECASE)
    assert not stray, f"Found & invocations of nssm not using $nssm: {stray}"


def test_nssm_resolved_once_into_single_variable():
    """The resolution chain must populate exactly one variable ($nssm) that
    is then reused everywhere -- not re-resolved per call site."""
    text = _source()
    assignments = re.findall(r"^\$nssm\s*=", text, re.MULTILINE)
    # $nssm is assigned in the resolution chain (may be assigned multiple
    # times across the chain's if/elseif branches, but never inside the
    # install/config section below the resolution block).
    resolution_end = text.index("Say \"Using NSSM at $nssm")
    post_resolution = text[resolution_end:]
    post_resolution_assignments = re.findall(r"^\$nssm\s*=", post_resolution, re.MULTILINE)
    assert not post_resolution_assignments, (
        "$nssm must be resolved once in the fallback chain and never "
        "reassigned afterward."
    )
    assert assignments, "$nssm must be assigned somewhere in the resolution chain."
