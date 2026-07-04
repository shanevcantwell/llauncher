"""Unit tests for ``llauncher.core.agent_token._generate_and_persist_token``.

Covers the chmod-OSError best-effort fallbacks (security hardening §3 C1):
on filesystems where ``chmod`` is a no-op or fails (e.g. NTFS via WSL), the
parent-dir 0700 and file 0600 hardening steps must degrade silently rather
than abort token materialization. The file write itself is the load-bearing
step and must still succeed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from llauncher.core.agent_token import _generate_and_persist_token


def test_generate_token_survives_chmod_oserror(tmp_path: Path) -> None:
    """Both ``chmod`` calls raising ``OSError`` still yields a written token.

    Patching ``Path.chmod`` to always raise exercises the parent-dir branch
    (lines 110-114) on the first call and the file branch (lines 122-123) on
    the second; the token is generated, persisted, and returned regardless.
    """
    target = tmp_path / "state" / "agent.token"

    with patch.object(Path, "chmod", side_effect=OSError("chmod unsupported")):
        token = _generate_and_persist_token(target)

    assert token
    assert target.exists()
    # File content is the token plus the trailing newline the writer appends.
    assert target.read_text(encoding="utf-8").strip() == token


def test_generate_token_chmods_when_supported(tmp_path: Path) -> None:
    """Happy path: chmod succeeds, token persisted (no fallback taken)."""
    target = tmp_path / "state" / "agent.token"

    token = _generate_and_persist_token(target)

    assert token
    assert target.exists()
    assert target.read_text(encoding="utf-8").strip() == token
