"""Regression guard: CRLF + BOM tolerance for token/env reads (issue #310).

**Concrete failure this pins.** A Windows-authored ``agent.env`` (CRLF line
endings, optionally a UTF-8 BOM from PowerShell's ``-Encoding utf8``) read by
Linux-side tooling. A naive ``grep``-and-use of the token line captured a
trailing ``\\r`` into the token, which broke HTTP header framing
(``X-Api-Key: <token>\\r`` → ``400 Invalid HTTP request``) — echoing the
BOM-in-``X-Api-Key`` failure from #127. ``PARSE-AT-THE-DOOR``: normalize once
at ingress (``utf-8-sig`` decode + line-ending-agnostic splitting), never
dual-parse or hand-strip ``\\r`` downstream.

This suite feeds a single CRLF+BOM fixture (mirroring a real
PowerShell-written ``agent.env``) through every read site touched by #310's
audit and asserts every parsed value is clean — no ``\\r``, no ``\\ufeff``,
in either the value or the key.

Scope, matching the issue's audit:

- ``llauncher.core.agent_token``: ``parse_env_file``, ``_read_env_file_token``,
  ``count_env_file_token_lines``, ``_strip_token_lines`` — the primary
  ``agent.env`` parsers.
- ``llauncher.remote.registry``: ``NODES_FILE`` / ``NODE_TOKENS_FILE`` JSON
  reads — sibling token-adjacent state files under the same
  ``LAUNCHER_STATE_DIR`` that were BOM-fragile (bare ``read_text()``, no
  ``utf-8-sig``) before this fix; a BOM there raised
  ``json.JSONDecodeError`` rather than silently corrupting a value, but it
  is the same "assumes no BOM" defect class this issue calls out.
- ``llauncher.core.config``: ``CONFIG_PATH`` (``config.json``) — the third
  hand-editable state file under ``LAUNCHER_STATE_DIR``, surfaced by the
  dispatched review of PR #326 as missed by the first audit pass. A BOM
  there was swallowed by ``ConfigStore.load``'s ``JSONDecodeError`` handler
  and silently returned ``{}`` — an *empty model list*, worse than a loud
  failure. Same fix (``utf-8-sig`` at the door).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llauncher.core.agent_token import (
    _read_env_file_token,
    _strip_token_lines,
    count_env_file_token_lines,
    parse_env_file,
)

# A realistic Windows-authored agent.env: UTF-8 BOM at the head, CRLF line
# endings throughout, a comment line, and the token line itself CRLF-terminated.
_CRLF_BOM_ENV_TEXT = (
    "# agent.env -- written by scripts/windows/install.ps1\r\n"
    "LLAUNCHER_AGENT_HOST=127.0.0.1\r\n"
    "LLAUNCHER_AGENT_TOKEN=windows-issued-token-abc123\r\n"
)
_CRLF_BOM_ENV_BYTES = b"\xef\xbb\xbf" + _CRLF_BOM_ENV_TEXT.encode("utf-8")


def _write_fixture(tmp_path: Path, name: str = "agent.env") -> Path:
    path = tmp_path / name
    path.write_bytes(_CRLF_BOM_ENV_BYTES)
    return path


class TestParseEnvFileCrlfBom:
    """``parse_env_file`` on a CRLF+BOM fixture yields clean values."""

    def test_values_have_no_carriage_return_or_bom(self, tmp_path: Path) -> None:
        path = _write_fixture(tmp_path)

        result = parse_env_file(path)

        assert result == {
            "LLAUNCHER_AGENT_HOST": "127.0.0.1",
            "LLAUNCHER_AGENT_TOKEN": "windows-issued-token-abc123",
        }
        for key, value in result.items():
            assert "\r" not in key
            assert "\r" not in value
            assert "﻿" not in key
            assert "﻿" not in value

    def test_comment_line_is_skipped_despite_crlf(self, tmp_path: Path) -> None:
        path = _write_fixture(tmp_path)

        result = parse_env_file(path)

        assert not any(k.startswith("#") for k in result)
        assert len(result) == 2


class TestReadEnvFileTokenCrlfBom:
    """``_read_env_file_token`` (the token-scoped wrapper) is CRLF+BOM clean."""

    def test_token_is_bare_no_crlf_no_bom(self, tmp_path: Path) -> None:
        path = _write_fixture(tmp_path)

        token = _read_env_file_token(path)

        assert token == "windows-issued-token-abc123"
        assert token is not None
        assert "\r" not in token
        assert "\n" not in token
        assert "﻿" not in token

    def test_token_survives_header_encoding(self, tmp_path: Path) -> None:
        """The failure this issue names: a trailing \\r breaks HTTP header
        framing. Assert the token round-trips through the exact machinery
        an HTTP client uses to build a header value (latin-1, per RFC 7230)
        without raising and without smuggling a CR/LF into the header.
        """
        path = _write_fixture(tmp_path)
        token = _read_env_file_token(path)
        assert token is not None

        header_value = f"{token}"
        # This is exactly the class of exception a raw \r would eventually
        # trip elsewhere in the stack (header-injection guards / encoders);
        # asserting cleanliness directly is the precise, stable guard.
        header_value.encode("latin-1")  # must not raise
        assert "\r" not in header_value and "\n" not in header_value


class TestCountEnvFileTokenLinesCrlfBom:
    """The duplicate-token guard must count correctly under CRLF+BOM."""

    def test_single_token_line_counts_as_one(self, tmp_path: Path) -> None:
        path = _write_fixture(tmp_path)
        assert count_env_file_token_lines(path) == 1

    def test_duplicate_crlf_bom_lines_count_as_two(self, tmp_path: Path) -> None:
        text = (
            "LLAUNCHER_AGENT_TOKEN=first\r\n"
            "LLAUNCHER_AGENT_TOKEN=second\r\n"
        )
        path = tmp_path / "agent.env"
        path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))

        assert count_env_file_token_lines(path) == 2


class TestStripTokenLinesCrlfBom:
    """``_strip_token_lines`` preserves non-token CRLF lines verbatim."""

    def test_strips_token_keeps_other_crlf_lines(self, tmp_path: Path) -> None:
        path = _write_fixture(tmp_path)
        # _strip_token_lines operates on already-decoded text (utf-8-sig),
        # matching how _generate_and_persist_token calls it.
        decoded = path.read_text(encoding="utf-8-sig")

        preserved = _strip_token_lines(decoded)

        assert "LLAUNCHER_AGENT_TOKEN" not in preserved
        assert "LLAUNCHER_AGENT_HOST=127.0.0.1" in preserved
        assert "﻿" not in preserved


class TestRegistryJsonCrlfBom:
    """``registry.py``'s JSON state reads tolerate a BOM (issue #310 audit)."""

    def test_nodes_file_with_bom_loads_cleanly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from llauncher.remote import registry as registry_mod

        nodes_file = tmp_path / "nodes.json"
        payload = {
            "remote-a": {
                "name": "remote-a",
                "host": "192.168.1.50",
                "port": 8765,
            }
        }
        nodes_file.write_bytes(
            b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")
        )
        monkeypatch.setattr(registry_mod, "NODES_FILE", nodes_file)
        tokens_file = tmp_path / "node_tokens.json"
        monkeypatch.setattr(registry_mod, "NODE_TOKENS_FILE", tokens_file)
        agent_env_path = tmp_path / "agent.env-absent"
        monkeypatch.setattr(
            "llauncher.core.agent_token.default_env_path", lambda: agent_env_path
        )
        monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

        reg = registry_mod.NodeRegistry()

        node = reg.get_node("remote-a")
        assert node is not None
        assert node.host == "192.168.1.50"

    def test_node_tokens_file_with_bom_loads_cleanly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from llauncher.remote import registry as registry_mod

        nodes_file = tmp_path / "nodes.json"
        nodes_file.write_text(
            json.dumps(
                {
                    "remote-a": {
                        "name": "remote-a",
                        "host": "192.168.1.50",
                        "port": 8765,
                    }
                }
            )
        )
        tokens_file = tmp_path / "node_tokens.json"
        tokens_file.write_bytes(
            b"\xef\xbb\xbf" + json.dumps({"remote-a": "tok-bom"}).encode("utf-8")
        )
        monkeypatch.setattr(registry_mod, "NODES_FILE", nodes_file)
        monkeypatch.setattr(registry_mod, "NODE_TOKENS_FILE", tokens_file)
        agent_env_path = tmp_path / "agent.env-absent"
        monkeypatch.setattr(
            "llauncher.core.agent_token.default_env_path", lambda: agent_env_path
        )
        monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

        reg = registry_mod.NodeRegistry()

        node = reg.get_node("remote-a")
        assert node is not None
        assert node.api_key == "tok-bom"
        assert "﻿" not in node.api_key


class TestConfigStoreCrlfBom:
    """``ConfigStore.load`` tolerates a BOM-prefixed ``config.json``.

    Surfaced by the dispatched review of PR #326: ``config.json`` lives
    under the same ``LAUNCHER_STATE_DIR`` as ``nodes.json`` /
    ``node_tokens.json`` and is equally hand-editable, but its bare
    ``read_text()`` was missed by the first audit pass. The failure mode
    here is the nastiest of the three: ``load()`` swallows the
    ``JSONDecodeError`` and returns ``{}``, silently presenting an empty
    model store instead of failing loud.
    """

    _PAYLOAD = {
        "bom-model": {
            "name": "bom-model",
            "model_path": "/models/bom-model.gguf",
        }
    }

    def test_bom_prefixed_config_loads_models(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from llauncher.core.config import ConfigStore

        config_path = tmp_path / "config.json"
        config_path.write_bytes(
            b"\xef\xbb\xbf" + json.dumps(self._PAYLOAD).encode("utf-8")
        )
        monkeypatch.setattr("llauncher.core.config.CONFIG_PATH", config_path)

        models = ConfigStore.load()

        # Before the fix this was {} -- the BOM tripped json.loads and the
        # except-branch silently returned an empty store.
        assert set(models) == {"bom-model"}
        assert models["bom-model"].model_path == "/models/bom-model.gguf"
