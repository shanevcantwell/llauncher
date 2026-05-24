"""Regression tests for ``llauncher.agent.auth._read_token_file``.

Specifically guards the BOM-tolerance widening that landed after a
Windows operator hit ``UnicodeEncodeError: 'ascii' codec can't encode
character '\\ufeff' in position 0`` when the UI process serialized the
token into the ``X-Api-Key`` request header.

Failure chain that motivated the widening:

1. Windows PowerShell 5.1's ``Set-Content -Encoding utf8`` writes
   ``EF BB BF`` at the head of the file.
2. ``_read_token_file`` previously decoded as plain ``utf-8`` (so the
   BOM survived as ``U+FEFF``) and called ``.strip()`` (which does NOT
   strip ``U+FEFF`` — it's zero-width non-breaking space, not
   whitespace by Python's classification).
3. The resulting token had a leading ``\\ufeff`` that httpx tried to
   ASCII-encode when serializing the ``X-Api-Key`` header, raising.

The fix is two-pronged: ``scripts/windows/install.ps1`` writes without
a BOM, AND ``_read_token_file`` decodes as ``utf-8-sig`` so any
upstream writer that re-introduces a BOM cannot reach the header
encoder. These tests pin the reader half.
"""

from __future__ import annotations


def test_read_token_file_strips_utf8_bom(tmp_path):
    """A token file with a UTF-8 BOM yields the bare token (no ``\\ufeff``)."""
    from llauncher.agent.auth import _read_token_file

    token = "abc123-no-bom-here"
    path = tmp_path / "agent.token"
    # Bytes path so we have full control over the BOM placement.
    path.write_bytes(b"\xef\xbb\xbf" + token.encode("ascii"))

    result = _read_token_file(path)

    assert result == token
    assert result is not None
    assert "﻿" not in result, "BOM leaked into the returned token"


def test_read_token_file_strips_bom_and_trailing_newline(tmp_path):
    """The two stripping behaviors compose — BOM at head, ``\\n`` at tail."""
    from llauncher.agent.auth import _read_token_file

    token = "trailing-newline-token"
    path = tmp_path / "agent.token"
    path.write_bytes(b"\xef\xbb\xbf" + token.encode("ascii") + b"\n")

    assert _read_token_file(path) == token


def test_read_token_file_no_bom_still_works(tmp_path):
    """``utf-8-sig`` is a strict superset of ``utf-8`` for BOM-less input."""
    from llauncher.agent.auth import _read_token_file

    token = "plain-utf8-token"
    path = tmp_path / "agent.token"
    path.write_text(token, encoding="utf-8")  # no BOM

    assert _read_token_file(path) == token


def test_read_token_file_bom_only_returns_none(tmp_path):
    """A file containing only a BOM is treated as empty — returns ``None``."""
    from llauncher.agent.auth import _read_token_file

    path = tmp_path / "agent.token"
    path.write_bytes(b"\xef\xbb\xbf")  # BOM, then nothing

    assert _read_token_file(path) is None


def test_read_token_file_missing_returns_none(tmp_path):
    """Pre-existing behavior: a missing file returns ``None`` (not raises)."""
    from llauncher.agent.auth import _read_token_file

    assert _read_token_file(tmp_path / "does-not-exist") is None
