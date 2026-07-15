"""Static-source guard: the NSSM service's Application must be repointed
on every run, not only on fresh install.

Background
----------
``scripts/windows/install.ps1`` configures the NSSM-managed
``llauncher-agent`` service in two branches:

  - fresh install (``else`` branch): ``nssm install $ServiceName $VenvExe``
    registers the service AND sets Application in one call.
  - refresh of an existing service (``if (Get-Service ...)`` branch):
    only ``nssm stop`` runs; ``nssm install`` is never called again.

Application (the venv executable NSSM actually launches) was, pre-fix,
set ONLY by the fresh-install branch. Re-running install.ps1 from a
DIFFERENT clone on refresh updated ``AppDirectory`` (in the always-
applied config block) but left Application pointed at the ORIGINAL
clone's venv exe -- the service silently kept executing stale code from
a checkout the operator had moved on from. This cost the operator hours
to diagnose.

The invariant this guard pins: "the service executes the venv you
installed from" -- on every run, not just the first. That invariant was
prose-only before this test; this is the enforcement surface. If a
future edit removes the common-block ``Application`` set (e.g. during a
refactor that assumes ``nssm install`` already covered it), this test
must fail.

This module is pure static-source inspection (regex over the script
text) -- it requires no ``pwsh`` and is NOT skip-gated, unlike the
execution tests in ``test_install_ps1_dedupe.py``.
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


def _install_ps1_text() -> str:
    path = _repo_root() / "scripts" / "windows" / "install.ps1"
    return path.read_text(encoding="ascii")


def _common_config_block(text: str) -> str:
    """Return the "(Re-)apply configuration" block that runs on every
    invocation (both fresh-install and refresh), isolated from the
    branch above it that only fires on fresh install.

    Anchored on the block's own comment header through the first
    ``Say`` call that closes it ("Service configuration applied."), so
    a match inside the fresh-install ``else`` branch (which sets
    Application via ``nssm install`` before this block even starts)
    cannot leak in and produce a false pass.
    """
    match = re.search(
        r"# \(Re-\)apply configuration.*?Service configuration applied\.",
        text,
        re.DOTALL,
    )
    assert match, (
        "Could not locate the '(Re-)apply configuration' block in "
        "install.ps1 -- the anchor comment or closing Say message may "
        "have changed. Update this test's anchor regex."
    )
    return match.group(0)


def _nssm_line(verb: str, tail: str) -> re.Pattern[str]:
    """Compile a line-start-anchored pattern for an ACTIVE (not
    commented-out) ``& $nssm <verb> $ServiceName <tail>`` line.

    ``^[^\\S\\n]*&`` requires the line to START (re.MULTILINE) with
    optional leading horizontal whitespace and then the ``&`` call
    operator -- a leading ``#`` (a commented-out line) is rejected,
    since ``#`` is not ``&`` nor horizontal whitespace. Without this
    anchor a ``# & $nssm set ... Application $VenvExe`` comment would
    still satisfy an unanchored ``re.search`` and green the test even
    though the installer no longer runs the line (the Nit-2 false pass).

    ``tail`` is the whitespace-joined tokens after ``$ServiceName``
    (e.g. ``"Application $VenvExe"``); each is regex-escaped and joined
    on ``\\s+``.
    """
    tokens = r"\s+".join(re.escape(tok) for tok in tail.split())
    return re.compile(
        r"^[^\S\n]*&\s*\$nssm\s+" + re.escape(verb) + r"\s+\$ServiceName\s+" + tokens,
        re.MULTILINE,
    )


def _nssm_set(setting: str, value: str) -> re.Pattern[str]:
    """Convenience wrapper: an ACTIVE ``& $nssm set $ServiceName
    <setting> <value>`` line (see :func:`_nssm_line`)."""
    return _nssm_line("set", f"{setting} {value}")


def test_application_is_repointed_in_common_config_block() -> None:
    """Application must be set in the always-applied block, not only via
    ``nssm install`` in the fresh-install-only branch.

    A future edit that drops this line would silently reintroduce the
    write-once bug: refresh from a different clone would leave the
    service executing a stale venv exe.
    """
    block = _common_config_block(_install_ps1_text())

    assert _nssm_set("Application", "$VenvExe").search(block), (
        "install.ps1's common (always-applied) configuration block must "
        "set Application on every run with an ACTIVE (non-commented) line: "
        "'& $nssm set $ServiceName Application $VenvExe'. Without it, "
        "refreshing the service from a different clone silently leaves "
        "it executing the original clone's venv exe."
    )


def test_application_and_appdirectory_both_repointed_in_common_block() -> None:
    """Application and AppDirectory must BOTH be repointed in the common
    (always-applied) block -- both describe "which clone the service runs
    from" and must move together, or a refresh could point AppDirectory
    at a new clone while Application still executes the old one (the exact
    bug this guard exists to prevent).

    Co-presence in the common block is the real invariant. Their relative
    ORDER is not: they are independent NSSM writes against a stopped
    service, so ordering is incidental to correctness and is deliberately
    NOT asserted.
    """
    block = _common_config_block(_install_ps1_text())

    assert _nssm_set("Application", "$VenvExe").search(block), (
        "Application set (active line) not found in common config block."
    )
    assert _nssm_set("AppDirectory", "$ProjectDir").search(block), (
        "AppDirectory set (active line) not found in common config block."
    )


def test_fresh_install_still_sets_application_too() -> None:
    """Belt-and-suspenders: the fresh-install branch's own
    ``nssm install $ServiceName $VenvExe`` call must still be present
    (it is what registers the service in the first place; the common-
    block set above is what makes refresh correct too).
    """
    text = _install_ps1_text()
    assert _nssm_line("install", "$VenvExe").search(text), (
        "install.ps1's fresh-install branch must still call (as an active, "
        "non-commented line) 'nssm install $ServiceName $VenvExe' to "
        "register the service."
    )


def test_venv_exe_missing_guard_fails_loud() -> None:
    """A missing venv exe must fail visibly before any service
    configuration happens, never silently pin the service to a
    non-existent path.

    Pinned as a static-source check: install.ps1 must contain a
    ``Test-Path $VenvExe`` guard, and it must appear before the
    NSSM install/refresh block (line-order proxy for "runs first").
    """
    text = _install_ps1_text()

    guard_match = re.search(r"-not\s*\(Test-Path\s+\$VenvExe\)", text)
    assert guard_match, (
        "install.ps1 must fail loud when $VenvExe is missing via a "
        "'-not (Test-Path $VenvExe)' guard -- a missing venv exe must "
        "never be silently pinned into the NSSM service."
    )

    nssm_block_match = re.search(r"nssm\s+(install|set)\s+\$ServiceName", text)
    assert nssm_block_match, "Could not locate the NSSM install/set block."

    assert guard_match.start() < nssm_block_match.start(), (
        "The Test-Path $VenvExe guard must run BEFORE any NSSM "
        "install/set call -- it must fail loud ahead of service "
        "configuration, not after."
    )


def test_venv_exe_missing_guard_exits_nonzero() -> None:
    """The missing-venv-exe guard must exit non-zero (via the script's
    existing Die helper), matching the script's established
    fail-and-exit style rather than warning and continuing.
    """
    text = _install_ps1_text()

    guard_match = re.search(
        r"-not\s*\(Test-Path\s+\$VenvExe\)\)\s*\{(.*?)\}", text, re.DOTALL
    )
    assert guard_match, "Could not locate the Test-Path $VenvExe guard body."
    body = guard_match.group(1)

    assert "Die" in body, (
        "The Test-Path $VenvExe guard must call the script's Die helper "
        "(Write-Host + exit 1) to fail loud and non-zero, matching the "
        "existing -Uninstall / nssm-not-found error style."
    )
