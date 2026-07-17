"""Unit tests for issue #284 — single live source (``agent.env``).

Covers the surface introduced/changed by #284:

- ``parse_env_file``: the ``KEY=VALUE`` reader itself — blank lines,
  comments, duplicate-key last-wins (matching systemd's
  ``EnvironmentFile=`` semantics, issue #285), UTF-8 BOM tolerance, a
  missing file, and an empty value.
- ``resolve_agent_token`` precedence with the renamed ``env_path``
  parameter: explicit env var still wins; ``agent.env`` is the fallback
  source; auto-generate persists *into* ``agent.env`` (not a standalone
  file).
- **Regression, behavioral not grep**: nothing in the resolution path
  reads a standalone ``agent.token`` file even when one is present and
  populated — the retired mirror class must have zero read-side effect
  on the resolved token.
"""

from __future__ import annotations

from pathlib import Path

from llauncher.core.agent_token import (
    _generate_and_persist_token,
    _read_env_file_token,
    parse_env_file,
    resolve_agent_token,
)


# ─────────── parse_env_file: KEY=VALUE reader semantics ────────────────


def test_parse_env_file_basic_key_value(tmp_path: Path) -> None:
    path = tmp_path / "agent.env"
    path.write_text("LLAUNCHER_AGENT_TOKEN=abc123\nLLAUNCHER_AGENT_PORT=8765\n")

    result = parse_env_file(path)

    assert result == {"LLAUNCHER_AGENT_TOKEN": "abc123", "LLAUNCHER_AGENT_PORT": "8765"}


def test_parse_env_file_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "agent.env"
    path.write_text("\n\nLLAUNCHER_AGENT_TOKEN=abc123\n\n\n")

    assert parse_env_file(path) == {"LLAUNCHER_AGENT_TOKEN": "abc123"}


def test_parse_env_file_skips_comment_lines(tmp_path: Path) -> None:
    path = tmp_path / "agent.env"
    path.write_text(
        "# this is a comment\n"
        "LLAUNCHER_AGENT_TOKEN=abc123\n"
        "  # indented comment, still skipped\n"
    )

    assert parse_env_file(path) == {"LLAUNCHER_AGENT_TOKEN": "abc123"}


def test_parse_env_file_duplicate_key_last_wins(tmp_path: Path) -> None:
    """Matches systemd's EnvironmentFile= semantics exactly (issue #285)."""
    path = tmp_path / "agent.env"
    path.write_text(
        "LLAUNCHER_AGENT_TOKEN=first-value\n"
        "LLAUNCHER_AGENT_TOKEN=second-value\n"
        "LLAUNCHER_AGENT_TOKEN=last-value\n"
    )

    assert parse_env_file(path)["LLAUNCHER_AGENT_TOKEN"] == "last-value"


def test_parse_env_file_bom_stripped_from_first_key(tmp_path: Path) -> None:
    """A leading UTF-8 BOM (PowerShell 5.1 ``-Encoding utf8``) must not
    leak into the first key's name or value."""
    path = tmp_path / "agent.env"
    path.write_bytes(b"\xef\xbb\xbfLLAUNCHER_AGENT_TOKEN=abc123\n")

    result = parse_env_file(path)

    assert result == {"LLAUNCHER_AGENT_TOKEN": "abc123"}
    assert "LLAUNCHER_AGENT_TOKEN" in result  # not "﻿LLAUNCHER_AGENT_TOKEN"


def test_parse_env_file_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    assert parse_env_file(tmp_path / "does-not-exist.env") == {}


def test_parse_env_file_empty_value(tmp_path: Path) -> None:
    path = tmp_path / "agent.env"
    path.write_text("LLAUNCHER_AGENT_NODE_NAME=\nLLAUNCHER_AGENT_TOKEN=abc123\n")

    result = parse_env_file(path)

    assert result["LLAUNCHER_AGENT_NODE_NAME"] == ""
    assert result["LLAUNCHER_AGENT_TOKEN"] == "abc123"


def test_parse_env_file_line_without_equals_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "agent.env"
    path.write_text("not-a-valid-line\nLLAUNCHER_AGENT_TOKEN=abc123\n")

    assert parse_env_file(path) == {"LLAUNCHER_AGENT_TOKEN": "abc123"}


def test_parse_env_file_line_with_empty_key_is_skipped(tmp_path: Path) -> None:
    """A line like ``=value`` (no key before the ``=``) is malformed and
    skipped rather than producing a bogus ``""`` dict key."""
    path = tmp_path / "agent.env"
    path.write_text("=orphan-value\nLLAUNCHER_AGENT_TOKEN=abc123\n")

    result = parse_env_file(path)

    assert result == {"LLAUNCHER_AGENT_TOKEN": "abc123"}
    assert "" not in result


def test_read_env_file_token_empty_value_counts_as_absent(tmp_path: Path) -> None:
    path = tmp_path / "agent.env"
    path.write_text("LLAUNCHER_AGENT_TOKEN=\n")

    assert _read_env_file_token(path) is None


# ─────────── resolve_agent_token: precedence with env_path ──────────────


def test_resolve_agent_token_env_var_wins_over_env_file(tmp_path, monkeypatch):
    """Explicit LLAUNCHER_AGENT_TOKEN always wins, even with a different
    value present in agent.env."""
    env_path = tmp_path / "agent.env"
    env_path.write_text("LLAUNCHER_AGENT_TOKEN=from-file\n")
    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "from-explicit-env")

    result = resolve_agent_token(env_path=env_path)

    assert result == "from-explicit-env"


def test_resolve_agent_token_falls_back_to_env_file(tmp_path, monkeypatch):
    env_path = tmp_path / "agent.env"
    env_path.write_text("LLAUNCHER_AGENT_TOKEN=from-file\n")
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

    result = resolve_agent_token(env_path=env_path)

    assert result == "from-file"


def test_resolve_agent_token_generates_into_env_file_when_absent(tmp_path, monkeypatch):
    """allow_generate=True with no env/file token creates agent.env with
    a LLAUNCHER_AGENT_TOKEN= line (not a standalone token file)."""
    env_path = tmp_path / "agent.env"
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    assert not env_path.exists()

    result = resolve_agent_token(env_path=env_path, allow_generate=True)

    assert result
    assert env_path.exists()
    assert env_path.read_text().strip() == f"LLAUNCHER_AGENT_TOKEN={result}"


def test_resolve_agent_token_no_generate_returns_none(tmp_path, monkeypatch):
    env_path = tmp_path / "agent.env"
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

    result = resolve_agent_token(env_path=env_path, allow_generate=False)

    assert result is None
    assert not env_path.exists(), "allow_generate=False must not create the file"


# ─────────── Regression (behavioral): agent.token is never read ────────


def test_standalone_agent_token_file_is_never_consulted(tmp_path, monkeypatch):
    """A populated, sibling ``agent.token`` file must have ZERO effect on
    resolution — proven behaviorally (the resolved value must differ from
    the mirror's content), not merely by grepping the source for reads.

    This is the direct regression guard for issue #284: the mirror class
    used to be a second read path an installer had to keep in sync; the
    runtime must now be blind to its existence entirely.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    stale_mirror = state_dir / "agent.token"
    stale_mirror.write_text("stale-mirror-value-must-not-be-read")

    env_path = state_dir / "agent.env"
    env_path.write_text("LLAUNCHER_AGENT_TOKEN=live-source-value\n")
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

    result = resolve_agent_token(env_path=env_path, allow_generate=False)

    assert result == "live-source-value"
    assert result != "stale-mirror-value-must-not-be-read"
    # The mirror is untouched — resolution neither reads nor mutates it.
    assert stale_mirror.read_text() == "stale-mirror-value-must-not-be-read"


def test_generate_path_ignores_sibling_agent_token_file(tmp_path, monkeypatch):
    """Even when agent.env is absent (triggering generate-and-persist), a
    sibling agent.token with a real value must NOT be adopted — the
    generator mints fresh and writes to agent.env, never falling back to
    the retired mirror shape.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    stale_mirror = state_dir / "agent.token"
    stale_mirror.write_text("stale-mirror-value")

    env_path = state_dir / "agent.env"
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    assert not env_path.exists()

    result = resolve_agent_token(env_path=env_path, allow_generate=True)

    assert result != "stale-mirror-value"
    assert env_path.exists()
    assert env_path.read_text().strip() == f"LLAUNCHER_AGENT_TOKEN={result}"


def test_generate_and_persist_token_never_touches_token_file_name(tmp_path):
    """Direct unit check on the persistence primitive: given an agent.env
    path, the function writes exactly that path — never a sibling named
    agent.token."""
    env_path = tmp_path / "agent.env"
    sibling_token_path = tmp_path / "agent.token"

    _generate_and_persist_token(env_path)

    assert env_path.exists()
    assert not sibling_token_path.exists()
