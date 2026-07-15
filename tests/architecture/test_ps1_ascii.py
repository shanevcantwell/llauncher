"""Static guard: every repo ``.ps1`` must be pure ASCII.

Why this test exists
--------------------
``scripts/windows/install.ps1`` shipped as BOM-less UTF-8 containing
em-dashes (``U+2014``, bytes ``E2 80 94``). Windows PowerShell 5.1 reads a
BOM-less script as the system ANSI codepage (CP1252), not UTF-8, so each
em-dash mis-decodes into three characters and the tokenizer trips -- the
installer fails to parse at all (#300).

The pwsh-gated CI check runs only where a real ``pwsh`` is present; on the
Linux runner it skips, which is exactly why #296's PS1 changes shipped
unverified. This guard closes that gap: it runs on any Linux runner with
no ``pwsh`` and fails the class -- non-ASCII in a ``.ps1`` -- before it can
reach a PS 5.1 field parse. ASCII sidesteps the codepage question entirely;
an ASCII-only script needs no BOM and cannot be mis-decoded.

Prose ("no Unicode in .ps1") erodes; this test does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Repo root = two parents up from this file: tests/architecture/<file>.
_REPO_ROOT = Path(__file__).resolve().parents[2]

_PS1_FILES = sorted(
    p for p in _REPO_ROOT.rglob("*.ps1") if ".git" not in p.parts
)


def _first_non_ascii(text: str) -> tuple[int, int, str] | None:
    """Return (1-based line, 1-based column, char) of the first non-ASCII
    character in ``text``, or ``None`` if the text is pure ASCII.
    """
    for line_no, line in enumerate(text.splitlines(), start=1):
        for col_no, ch in enumerate(line, start=1):
            if ord(ch) > 0x7F:
                return line_no, col_no, ch
    return None


def test_ps1_files_discovered() -> None:
    """Guard the guard: if no ``.ps1`` is ever found, the parametrized
    check below silently passes and protects nothing. This repo ships
    Windows installer scripts, so the glob must be non-empty.
    """
    assert _PS1_FILES, (
        "No .ps1 files found under the repo root; the ASCII guard would "
        "vacuously pass. Check the glob in tests/architecture/test_ps1_ascii.py."
    )


@pytest.mark.parametrize("ps1_path", _PS1_FILES, ids=lambda p: p.name)
def test_ps1_is_pure_ascii(ps1_path: Path) -> None:
    """Every ``.ps1`` must decode as pure ASCII.

    A byte outside ``0x00..0x7F`` is a defect: under Windows PowerShell
    5.1 a BOM-less non-ASCII script is decoded as CP1252 and mis-parses
    (#300). Read as latin-1 so no byte is lost or replaced -- every byte
    maps 1:1 to a codepoint, making ``ord(ch) > 0x7F`` an exact test for
    a non-ASCII byte and letting the message report the true offset.
    """
    text = ps1_path.read_text(encoding="latin-1")
    hit = _first_non_ascii(text)
    rel = ps1_path.relative_to(_REPO_ROOT)
    assert hit is None, (
        f"{rel}:{hit[0]}:{hit[1]} contains non-ASCII character "
        f"{hit[2]!r} (U+{ord(hit[2]):04X}). Windows PowerShell 5.1 reads a "
        f"BOM-less script as CP1252 and mis-decodes it (#300). Replace it "
        f"with an ASCII equivalent (e.g. em-dash -> '--')."
    )
