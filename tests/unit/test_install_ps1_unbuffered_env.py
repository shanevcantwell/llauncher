"""Static-source guard for issue #128 (Windows runtime logging).

``scripts/windows/install.ps1`` builds ``$envPairs`` from the live
``agent.env`` file, then unconditionally appends ``LAUNCHER_STATE_DIR``
(the LocalSystem wrinkle, issue #284) before feeding the array to NSSM's
``AppEnvironmentExtra``. This mirrors that pattern to also unconditionally
append ``PYTHONUNBUFFERED=1`` -- NSSM redirects agent stdout/stderr to
files, and a redirected (non-TTY) Python stream is block-buffered by
default, so without this the agent's runtime log lines sit in an
in-process buffer indefinitely instead of reaching those files.

``PYTHONUNBUFFERED`` must be set before the interpreter starts (NSSM
``AppEnvironmentExtra``), not merely documented in ``agent.env`` (which
``load_dotenv()`` only reads after Python is already running -- too late
for an interpreter-level variable).

These are static-source (text) assertions, not a ``pwsh``-execution test,
so they run on any host without needing PowerShell installed -- same
posture as ``tests/architecture/test_ps1_ascii.py``.
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


def test_pythonunbuffered_appended_to_env_pairs():
    """``$envPairs += "PYTHONUNBUFFERED=1"`` appears, mirroring the
    existing LAUNCHER_STATE_DIR append."""
    text = _source()
    assert re.search(
        r'\$envPairs\s*\+=\s*"PYTHONUNBUFFERED=1"', text
    ), (
        "install.ps1 must unconditionally append PYTHONUNBUFFERED=1 to "
        "$envPairs (issue #128), mirroring the LAUNCHER_STATE_DIR append "
        "(issue #284)."
    )


def test_pythonunbuffered_append_precedes_nssm_set():
    """The append must happen before the ``AppEnvironmentExtra`` NSSM call
    consumes ``$envPairs`` -- appending after would be silently inert."""
    text = _source()
    append_match = re.search(r'\$envPairs\s*\+=\s*"PYTHONUNBUFFERED=1"', text)
    # Anchor to the actual NSSM invocation (`& $nssm set ... AppEnvironmentExtra`),
    # not any of the several comment lines that also mention the name.
    nssm_match = re.search(r"&\s*\$nssm\s+set\s+\$ServiceName\s+AppEnvironmentExtra", text)
    assert append_match is not None
    assert nssm_match is not None
    assert append_match.start() < nssm_match.start(), (
        "PYTHONUNBUFFERED=1 must be appended to $envPairs before the "
        "AppEnvironmentExtra NSSM call consumes it."
    )


def test_pythonunbuffered_append_is_unconditional():
    """The append must not be gated behind an ``if`` -- there is no
    scenario where an operator wants the pre-#128 buffering bug back."""
    text = _source()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.search(r'\$envPairs\s*\+=\s*"PYTHONUNBUFFERED=1"', line):
            # None of the immediately preceding non-comment/non-blank
            # lines back this into a conditional block.
            preceding = [
                ln.strip()
                for ln in lines[max(0, i - 5) : i]
                if ln.strip() and not ln.strip().startswith("#")
            ]
            assert not any(p.startswith("if ") or p.startswith("if(") for p in preceding), (
                "PYTHONUNBUFFERED=1 append appears to be gated behind an "
                "`if` -- it must be unconditional."
            )
            return
    raise AssertionError("PYTHONUNBUFFERED=1 append line not found")
