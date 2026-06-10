"""Regression guards for the LLAUNCHER_* env-var naming (issues #138/#151).

Background
----------
Issue #138 renamed every ``LAUNCHER_AGENT_*`` env var to
``LLAUNCHER_AGENT_*`` (matching the project name ``llauncher``); issue
#151 finished the job for the rest of the single-L surface — the
``core/settings.py`` path/log/cache family and the runner scripts' UI
bind address. The original typo was introduced by an LLM (qwen3.5 /
qwen3.6 family) that silently drops the leading token when generating
identifiers from the project name. Any future code-generating model
ingesting this codebase is at risk of reintroducing the typo — and a
green test suite would not catch it, because the new (correct) name
still works fine in isolation.

This module is the regression net. It has five jobs:

Category A — pin the precedence in :func:`resolve_agent_token`:
    the new name is honored, the old name is *not* honored as a
    fallback, and the new name wins when both are set.

Category B — pin the precedence for the other agent env reads
    (``LLAUNCHER_AGENT_HOST`` / ``_PORT`` / ``_NODE_NAME``) via the
    clean seams :class:`AgentConfig.from_env` and
    :func:`llauncher.agent.routing.get_node_name`.

Category C — repo-grep guard: fail if any legacy single-L
    ``LAUNCHER_*`` name reappears in tracked source outside an
    allowlist of historical paths (changelog, handoffs, plans,
    reviews).

Category D — pin the ``core/settings.py`` family (#151): each
    ``LLAUNCHER_{RUN_DIR,AUDIT_PATH,LOG_DIR,LOG_MAX_BYTES,LOG_KEEP,
    FOOTER_CACHE_S}`` is honored at settings (re)import, and the
    legacy single-L spelling is silently ignored — the documented
    default applies (same no-fallback posture as #138).

Category E — pin the UI bind address (#151): ``LLAUNCHER_UI_HOST``
    is the only env read; the legacy single-L spelling falls through
    to the loopback default.

ALLOWED_PATH_PREFIXES rationale
-------------------------------
The allowlist is the set of paths where matches are *expected*:
    - ``CHANGELOG.md`` documents the rename and shows the old → new
      mapping plus the operator's sed/PowerShell migration snippet.
    - ``docs/handoffs/``, ``docs/reviews/``, ``docs/plans/`` are
      historical narrative — they describe the codebase as it was at
      a point in time and must not be rewritten.
    - ``docs/_audit_adrs.md`` and ``docs/v2-handoff.md`` are
      time-stamped audit / handoff snapshots in the same category.
    - This test file itself is allowlisted because the assertion
      message includes the legacy token by construction (the
      split-string trick covers the search pattern but the message
      formatting may still emit the literal).
"""

from __future__ import annotations

import importlib
import io
import subprocess
from pathlib import Path

import pytest


# ─────────── Category A: resolve_agent_token env precedence ────────


def test_resolve_uses_new_env_var_name(monkeypatch, tmp_path):
    """The new ``LLAUNCHER_AGENT_TOKEN`` is read and returned verbatim."""
    from llauncher.agent.auth import resolve_agent_token

    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "correct-horse-battery-staple")
    monkeypatch.delenv("LAUNCHER_AGENT_TOKEN", raising=False)

    result = resolve_agent_token(token_path=tmp_path / "agent.token")

    assert result == "correct-horse-battery-staple"


def test_resolve_ignores_old_env_var_name(monkeypatch, tmp_path):
    """The legacy ``LAUNCHER_AGENT_TOKEN`` MUST NOT be honored as a fallback.

    With only the typo set, no token file, and ``allow_generate=False``,
    the resolver must return ``None`` — any other result means a
    silent dual-read crept back in.
    """
    from llauncher.agent.auth import resolve_agent_token

    monkeypatch.setenv("LAUNCHER_AGENT_TOKEN", "stale-typo-value")
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    token_path = tmp_path / "agent.token"
    assert not token_path.exists()

    result = resolve_agent_token(token_path=token_path, allow_generate=False)

    assert result is None


def test_resolve_prefers_new_when_both_set(monkeypatch, tmp_path):
    """Belt-and-suspenders: if a future dual-read sneaks in, the new name wins."""
    from llauncher.agent.auth import resolve_agent_token

    monkeypatch.setenv("LAUNCHER_AGENT_TOKEN", "stale")
    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "fresh")

    result = resolve_agent_token(token_path=tmp_path / "agent.token")

    assert result == "fresh"


def test_resolve_stdin_trigger_uses_new_name(monkeypatch, tmp_path):
    """``LLAUNCHER_AGENT_TOKEN=-`` triggers the stdin read path."""
    from llauncher.agent import auth as auth_mod

    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "-")
    monkeypatch.delenv("LAUNCHER_AGENT_TOKEN", raising=False)
    monkeypatch.setattr(auth_mod.sys, "stdin", io.StringIO("piped-token\n"))

    result = auth_mod.resolve_agent_token(token_path=tmp_path / "agent.token")

    assert result == "piped-token"


def test_resolve_stdin_trigger_old_name_does_not_fire(monkeypatch, tmp_path):
    """Setting the legacy name to ``-`` must NOT trigger the stdin read path.

    If the old name were silently honored, the resolver would block on
    ``sys.stdin.readline()`` waiting for input. We deliberately do NOT
    patch stdin — a return of ``None`` proves the legacy name was
    ignored end-to-end.
    """
    from llauncher.agent.auth import resolve_agent_token

    monkeypatch.setenv("LAUNCHER_AGENT_TOKEN", "-")
    monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
    token_path = tmp_path / "agent.token"
    assert not token_path.exists()

    result = resolve_agent_token(token_path=token_path, allow_generate=False)

    assert result is None


# ─────────── Category B: server-side env reads (HOST / PORT / NODE_NAME) ────


def test_agent_config_from_env_uses_new_host_name(monkeypatch):
    from llauncher.agent.config import AgentConfig

    monkeypatch.setenv("LLAUNCHER_AGENT_HOST", "0.0.0.0")
    monkeypatch.delenv("LAUNCHER_AGENT_HOST", raising=False)

    assert AgentConfig.from_env().host == "0.0.0.0"


def test_agent_config_from_env_ignores_old_host_name(monkeypatch):
    """Legacy ``LAUNCHER_AGENT_HOST`` MUST fall through to the default."""
    from llauncher.agent.config import AgentConfig

    monkeypatch.setenv("LAUNCHER_AGENT_HOST", "0.0.0.0")
    monkeypatch.delenv("LLAUNCHER_AGENT_HOST", raising=False)

    assert AgentConfig.from_env().host == "127.0.0.1"


def test_agent_config_from_env_uses_new_port_name(monkeypatch):
    from llauncher.agent.config import AgentConfig

    monkeypatch.setenv("LLAUNCHER_AGENT_PORT", "9000")
    monkeypatch.delenv("LAUNCHER_AGENT_PORT", raising=False)

    assert AgentConfig.from_env().port == 9000


def test_agent_config_from_env_ignores_old_port_name(monkeypatch):
    """Legacy ``LAUNCHER_AGENT_PORT`` MUST fall through to the default (8765)."""
    from llauncher.agent.config import AgentConfig

    monkeypatch.setenv("LAUNCHER_AGENT_PORT", "9000")
    monkeypatch.delenv("LLAUNCHER_AGENT_PORT", raising=False)

    assert AgentConfig.from_env().port == 8765


def test_agent_config_from_env_uses_new_node_name(monkeypatch):
    from llauncher.agent.config import AgentConfig

    monkeypatch.setenv("LLAUNCHER_AGENT_NODE_NAME", "fresh-node")
    monkeypatch.delenv("LAUNCHER_AGENT_NODE_NAME", raising=False)

    assert AgentConfig.from_env().node_name == "fresh-node"


def test_agent_config_from_env_ignores_old_node_name(monkeypatch):
    """Legacy ``LAUNCHER_AGENT_NODE_NAME`` MUST be ignored (yields ``None``)."""
    from llauncher.agent.config import AgentConfig

    monkeypatch.setenv("LAUNCHER_AGENT_NODE_NAME", "stale-node")
    monkeypatch.delenv("LLAUNCHER_AGENT_NODE_NAME", raising=False)

    assert AgentConfig.from_env().node_name is None


def test_routing_get_node_name_uses_new_name(monkeypatch):
    from llauncher.agent.routing import get_node_name

    monkeypatch.setenv("LLAUNCHER_AGENT_NODE_NAME", "routing-fresh")
    monkeypatch.delenv("LAUNCHER_AGENT_NODE_NAME", raising=False)

    assert get_node_name() == "routing-fresh"


def test_routing_get_node_name_ignores_old_name(monkeypatch):
    """Legacy name must not leak into the routing layer's node-name lookup."""
    import socket

    from llauncher.agent.routing import get_node_name

    monkeypatch.setenv("LAUNCHER_AGENT_NODE_NAME", "routing-stale")
    monkeypatch.delenv("LLAUNCHER_AGENT_NODE_NAME", raising=False)

    # With only the typo set, get_node_name falls back to the hostname.
    assert get_node_name() == socket.gethostname()


# ─────────── Category C: repo-grep regression guard ────────────────


# Built from a split string so this source line does not itself match
# the legacy pattern under ``\bLAUNCHER_``. Belt-and-suspenders:
# this file is also in ``ALLOWED_PATH_PREFIXES`` below.
#
# #151 broadened this from the agent family (``LAUNCHER_AGENT_``) to
# the whole single-L namespace: any ``LAUNCHER_<UPPERCASE>`` token now
# trips the guard, including names that do not exist yet — a future
# feature minting a fresh env var under the typo prefix fails here
# before it ships.
_LEGACY_PREFIX = "LAUNCHER" + "_"

# Paths whose contents are historical records or explicitly document
# the migration. Matches here are expected and allowed. See the module
# docstring for the rationale on each entry.
ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    "docs/handoffs/",
    "docs/reviews/",
    "docs/plans/",
    "docs/_audit_adrs.md",
    "docs/v2-handoff.md",
    "CHANGELOG.md",
    "tests/unit/test_env_var_naming_regression.py",
)


def _repo_root() -> Path:
    """Walk up from this file until a ``.git`` directory is found."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError(
        "Could not locate repo root (no .git ancestor) from "
        f"{here}"
    )


def test_no_legacy_env_var_names_in_tracked_source():
    """No tracked source outside the historical allowlist may reference
    the legacy ``LAUNCHER_AGENT_*`` names.

    Uses ``git grep -nE`` with a word-boundary anchor so that
    ``LLAUNCHER_AGENT_*`` (the *correct* name, which contains the
    legacy substring) is not matched.
    """
    repo_root = _repo_root()

    # Word-boundary regex: matches LAUNCHER_<UPPER> only when not
    # preceded by a word character. That excludes the correct
    # LLAUNCHER_* form (where the preceding char is an L). The
    # trailing [A-Z] keeps prose like "launcher_" case-sensitive
    # noise out without loosening the guard.
    pattern = r"\b" + _LEGACY_PREFIX + "[A-Z]"

    result = subprocess.run(
        ["git", "grep", "-nE", "--", pattern],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    # git grep exits 1 when there are no matches — that's the
    # passing case. Any other non-zero exit is a real error.
    if result.returncode == 1 and not result.stdout:
        return
    if result.returncode not in (0, 1):
        pytest.fail(
            f"git grep failed with exit {result.returncode}: "
            f"{result.stderr.strip()}"
        )

    offenders: list[str] = []
    for line in result.stdout.splitlines():
        # Lines are "path:lineno:contents".
        path = line.split(":", 1)[0]
        if any(path.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES):
            continue
        offenders.append(line)

    if offenders:
        joined = "\n  ".join(offenders)
        pytest.fail(
            "Legacy env var name reappeared in tracked source "
            "(see issues #138/#151 — the rename to LLAUNCHER_* must "
            "not be undone). Offending occurrences:\n  " + joined
        )


# ─────────── Category D: core/settings.py family (#151) ────────────


# (new env name, raw env value, settings attribute, expected, default)
# ``expected`` is what the attribute must read back as when the NEW
# name is set to ``raw``; ``default`` is what it must fall back to
# when only the LEGACY single-L name is set.
_SETTINGS_FAMILY = [
    pytest.param(
        "LLAUNCHER_RUN_DIR", "/mnt/state/run",
        "LLAUNCHER_RUN_DIR", Path("/mnt/state/run"),
        Path.home() / ".llauncher" / "run",
        id="run-dir",
    ),
    pytest.param(
        "LLAUNCHER_AUDIT_PATH", "/mnt/state/audit.jsonl",
        "LLAUNCHER_AUDIT_PATH", Path("/mnt/state/audit.jsonl"),
        Path.home() / ".llauncher" / "audit.jsonl",
        id="audit-path",
    ),
    pytest.param(
        "LLAUNCHER_LOG_DIR", "/mnt/state/logs",
        "LLAUNCHER_LOG_DIR", Path("/mnt/state/logs"),
        Path.home() / ".llauncher" / "logs",
        id="log-dir",
    ),
    pytest.param(
        "LLAUNCHER_LOG_MAX_BYTES", "12345",
        "LLAUNCHER_LOG_MAX_BYTES", 12345,
        50 * 1024 * 1024,
        id="log-max-bytes",
    ),
    pytest.param(
        "LLAUNCHER_LOG_KEEP", "7",
        "LLAUNCHER_LOG_KEEP", 7,
        3,
        id="log-keep",
    ),
    pytest.param(
        "LLAUNCHER_FOOTER_CACHE_S", "2.5",
        "LLAUNCHER_FOOTER_CACHE_S", 2.5,
        1.0,
        id="footer-cache-s",
    ),
]


def _reload_settings():
    import llauncher.core.settings as settings_mod

    return settings_mod, importlib.reload(settings_mod)


@pytest.mark.parametrize(
    "env_name, raw, attr, expected, default", _SETTINGS_FAMILY
)
def test_settings_family_uses_new_env_var_name(
    monkeypatch, env_name, raw, attr, expected, default
):
    """Each ``LLAUNCHER_*`` settings var is honored at (re)import."""
    legacy_name = env_name.removeprefix("L")
    monkeypatch.setenv(env_name, raw)
    monkeypatch.delenv(legacy_name, raising=False)

    settings_mod, reloaded = _reload_settings()
    try:
        assert getattr(reloaded, attr) == expected
    finally:
        # Restore the un-monkey-patched module state for later tests.
        monkeypatch.delenv(env_name, raising=False)
        importlib.reload(settings_mod)


@pytest.mark.parametrize(
    "env_name, raw, attr, expected, default", _SETTINGS_FAMILY
)
def test_settings_family_ignores_legacy_env_var_name(
    monkeypatch, env_name, raw, attr, expected, default
):
    """A legacy single-L spelling MUST be silently ignored (#151).

    Same no-fallback posture as #138: with only the typo set, the
    documented default applies — any other value means a dual-read
    crept back in.
    """
    legacy_name = env_name.removeprefix("L")
    monkeypatch.setenv(legacy_name, raw)
    monkeypatch.delenv(env_name, raising=False)

    settings_mod, reloaded = _reload_settings()
    try:
        assert getattr(reloaded, attr) == default
    finally:
        monkeypatch.delenv(legacy_name, raising=False)
        importlib.reload(settings_mod)


# ─────────── Category E: UI bind address (#151) ────────────────────


def test_resolve_ui_host_ignores_legacy_name():
    """Legacy single-L UI-host spelling falls through to loopback.

    The new-name happy path is pinned by
    ``tests/unit/test_ui_launch.py``; this guard covers the
    legacy-ignored half only.
    """
    from llauncher.ui.launch import resolve_ui_host

    legacy_name = "L" "AUNCHER_UI_HOST"
    assert resolve_ui_host({legacy_name: "0.0.0.0"}) == "127.0.0.1"
