"""Unit tests for ``llauncher.core.agent_token._generate_and_persist_token``.

Covers the chmod-OSError best-effort fallbacks (security hardening §3 C1):
on filesystems where ``chmod`` is a no-op or fails (e.g. NTFS via WSL), the
parent-dir 0700 and file 0600 hardening steps must degrade silently rather
than abort token materialization. The file write itself is the load-bearing
step and must still succeed.

Issue #284 changed the persist target from a standalone ``agent.token``
file (bare value) to a ``LLAUNCHER_AGENT_TOKEN=<value>`` line appended
into (or seeding) ``agent.env`` — the single live source both the agent
and the UI parse directly. These tests were updated in place; the
chmod-degradation behavior under test is unchanged.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from llauncher.core.agent_token import _generate_and_persist_token


def test_generate_token_survives_chmod_oserror(tmp_path: Path) -> None:
    """Both ``chmod`` calls raising ``OSError`` still yields a written token.

    Patching ``Path.chmod`` to always raise exercises the parent-dir and
    file chmod fallbacks; the token is generated, persisted, and returned
    regardless.
    """
    target = tmp_path / "state" / "agent.env"

    with patch.object(Path, "chmod", side_effect=OSError("chmod unsupported")):
        token = _generate_and_persist_token(target)

    assert token
    assert target.exists()
    assert target.read_text(encoding="utf-8").strip() == f"LLAUNCHER_AGENT_TOKEN={token}"


def test_generate_token_chmods_when_supported(tmp_path: Path) -> None:
    """Happy path: chmod succeeds, token persisted (no fallback taken)."""
    target = tmp_path / "state" / "agent.env"

    token = _generate_and_persist_token(target)

    assert token
    assert target.exists()
    assert target.read_text(encoding="utf-8").strip() == f"LLAUNCHER_AGENT_TOKEN={token}"


def test_generate_token_appends_to_existing_env_file(tmp_path: Path) -> None:
    """Existing ``agent.env`` content (other keys) survives the append.

    The only way this function is reached is when ``agent.env`` exists but
    has no usable ``LLAUNCHER_AGENT_TOKEN=`` line — the append must
    preserve every prior line verbatim.
    """
    target = tmp_path / "agent.env"
    target.write_text("LLAUNCHER_AGENT_HOST=0.0.0.0\nLLAUNCHER_AGENT_PORT=8765\n")

    token = _generate_and_persist_token(target)

    text = target.read_text(encoding="utf-8")
    assert "LLAUNCHER_AGENT_HOST=0.0.0.0" in text
    assert "LLAUNCHER_AGENT_PORT=8765" in text
    assert f"LLAUNCHER_AGENT_TOKEN={token}" in text


def test_generate_token_append_inserts_newline_when_missing(tmp_path: Path) -> None:
    """A last existing line with no trailing newline must not fuse with
    the appended key.

    Without a separator guard, appending directly onto
    ``...PORT=8765`` (no trailing ``\\n``) would produce
    ``...PORT=8765LLAUNCHER_AGENT_TOKEN=<token>`` on one physical line —
    unparseable by ``parse_env_file`` (the whole line has no valid
    ``KEY=VALUE`` shape once a stray ``LLAUNCHER_AGENT_TOKEN=`` is fused
    into the previous value). This pins the fix: a missing trailing
    newline is detected and a separator is inserted before the append.
    """
    from llauncher.core.agent_token import parse_env_file

    target = tmp_path / "agent.env"
    target.write_text("LLAUNCHER_AGENT_PORT=8765")  # deliberately no \n

    token = _generate_and_persist_token(target)

    parsed = parse_env_file(target)
    assert parsed["LLAUNCHER_AGENT_PORT"] == "8765"
    assert parsed["LLAUNCHER_AGENT_TOKEN"] == token


def test_generate_token_append_preserves_existing_file_permissions(tmp_path: Path) -> None:
    """Appending to an existing agent.env must NOT tighten its mode.

    systemd ``--system`` mode's agent.env is 0640/group-``inference`` so
    the UI can read it via group membership; if this function ever
    silently re-chmod'd an existing file to 0600 it would break that
    cross-process read path the moment the agent auto-generates into an
    env file that has other keys but no token line yet.
    """
    import stat as stat_mod

    target = tmp_path / "agent.env"
    target.write_text("LLAUNCHER_AGENT_HOST=0.0.0.0\n")
    target.chmod(0o640)

    _generate_and_persist_token(target)

    mode = stat_mod.S_IMODE(target.stat().st_mode)
    assert mode == 0o640, f"expected append to preserve 0640, got {oct(mode)}"


# --- Rewrite-in-place / no-split-brain (issue #293) --------------------
#
# The generate/persist path is reached only when the file has no *usable*
# token line (empty or absent value; an empty ``LLAUNCHER_AGENT_TOKEN=``
# counts as absent). Before #293 it *appended* a token line unconditionally,
# so an existing empty/placeholder token line was left behind alongside the
# new one — two ``LLAUNCHER_AGENT_TOKEN=`` lines in one file. Every resolver
# is last-wins, but two token lines is the split-brain footgun that reopened
# the recurring UI-403: a later hand-edit reordering them makes the agent and
# the UI resolve different values. These pin the rewrite-in-place fix: exactly
# one canonical token line remains, and the value the *file* resolves to (what
# a client reading agent.env would get) equals the returned token (what the
# agent runs with) — no split.


def _resolved_file_token(path: Path) -> str | None:
    """The token a client would resolve by parsing agent.env (last-wins)."""
    from llauncher.core.agent_token import _read_env_file_token

    return _read_env_file_token(path)


def test_generate_token_strips_existing_empty_token_line_no_duplicate(
    tmp_path: Path,
) -> None:
    """An empty placeholder ``LLAUNCHER_AGENT_TOKEN=`` line is rewritten, not
    duplicated.

    This is the exact pre-#293 recurrence shape: a template seeded the file
    with an empty token line, the operator never filled it, so the agent
    reaches the generate path — and must leave the file with EXACTLY ONE
    token line (the fresh one), never append a second.
    """
    from llauncher.core.agent_token import count_env_file_token_lines

    target = tmp_path / "agent.env"
    target.write_text(
        "LLAUNCHER_AGENT_HOST=0.0.0.0\nLLAUNCHER_AGENT_TOKEN=\n"
    )

    token = _generate_and_persist_token(target)

    assert count_env_file_token_lines(target) == 1
    # Non-token lines preserved.
    assert "LLAUNCHER_AGENT_HOST=0.0.0.0" in target.read_text(encoding="utf-8")
    # Server-resolved (returned) == client-resolved (file parse): no split.
    assert _resolved_file_token(target) == token


def test_generate_token_no_split_between_server_and_client(tmp_path: Path) -> None:
    """The returned token equals the file-resolved token — the split-brain
    invariant.

    Pre-seed a file with an empty legacy-migrated token placeholder plus
    other keys; after generate, the value the agent runs with (return value)
    and the value a client resolves from agent.env must be identical.
    """
    target = tmp_path / "agent.env"
    target.write_text(
        "LLAUNCHER_AGENT_HOST=127.0.0.1\n"
        "LLAUNCHER_AGENT_TOKEN=\n"
        "LLAUNCHER_AGENT_PORT=8765\n"
    )

    token = _generate_and_persist_token(target)

    assert _resolved_file_token(target) == token
    assert token  # non-empty


def test_generate_token_ignores_commented_token_line(tmp_path: Path) -> None:
    """A commented ``# LLAUNCHER_AGENT_TOKEN=`` line is not treated as a token
    line and survives the rewrite verbatim.

    Guards the key-anchored strip: only a real ``KEY=`` line (optional
    leading whitespace) is a token line; a comment must not be stripped and
    must not count toward the duplicate guard.
    """
    from llauncher.core.agent_token import count_env_file_token_lines

    target = tmp_path / "agent.env"
    target.write_text(
        "# LLAUNCHER_AGENT_TOKEN=example-do-not-use\n"
        "LLAUNCHER_AGENT_HOST=0.0.0.0\n"
    )

    token = _generate_and_persist_token(target)

    text = target.read_text(encoding="utf-8")
    assert "# LLAUNCHER_AGENT_TOKEN=example-do-not-use" in text
    assert count_env_file_token_lines(target) == 1
    assert _resolved_file_token(target) == token


def test_generate_token_with_legacy_only_line_yields_single_canonical(
    tmp_path: Path,
) -> None:
    """A legacy-only ``LAUNCHER_AGENT_TOKEN`` line (single-L) is not read by
    the runtime, so generate is reached and writes ONE canonical line.

    The runtime never reads the legacy single-L key (#138/#139), so a file
    carrying only it has no usable canonical token — the agent generates
    one. The legacy line is left in place (the installer migrates it; the
    runtime is inert to it), but there must be exactly one *canonical*
    ``LLAUNCHER_AGENT_TOKEN=`` line afterward and no server/client split.
    """
    from llauncher.core.agent_token import count_env_file_token_lines

    target = tmp_path / "agent.env"
    target.write_text("LAUNCHER_AGENT_TOKEN=legacy-inert-value\n")

    token = _generate_and_persist_token(target)

    assert count_env_file_token_lines(target) == 1  # canonical only
    assert _resolved_file_token(target) == token
