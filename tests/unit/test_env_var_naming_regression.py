"""Regression guards for the LLAUNCHER_AGENT_* env-var naming (issue #138).

Background
----------
Issue #138 renamed every ``LAUNCHER_AGENT_*`` env var to
``LLAUNCHER_AGENT_*`` (matching the project name ``llauncher``). The
original typo was introduced by an LLM (qwen3.5 / qwen3.6 family) that
silently drops the leading token when generating identifiers from the
project name. Any future code-generating model ingesting this codebase
is at risk of reintroducing the typo — and a green test suite would
not catch it, because the new (correct) name still works fine in
isolation.

This module is the regression net. It has three jobs:

Category A — pin the precedence in :func:`resolve_agent_token`:
    the new name is honored, the old name is *not* honored as a
    fallback, and the new name wins when both are set.

Category B — pin the precedence for the other agent env reads
    (``LLAUNCHER_AGENT_HOST`` / ``_PORT`` / ``_NODE_NAME``) via the
    clean seams :class:`AgentConfig.from_env` and
    :func:`llauncher.agent.routing.get_node_name`.

Category C — repo-grep guard: fail if the legacy ``LAUNCHER_AGENT_*``
    names reappear in tracked source outside an allowlist of
    historical paths (changelog, handoffs, plans, reviews).

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
    - ``scripts/windows/install.ps1``, ``scripts/systemd/install.sh``,
      ``llauncher/core/agent_token.py``, ``llauncher/agent/server.py``,
      ``tests/integration/test_agent_security_c1_c2.py``,
      ``tests/unit/test_agent_token_legacy_env.py``, and
      ``docs/operations/run-as-a-service.md`` are allowlisted for issue
      #281: the pre-#139 legacy-key *migration and detection* logic
      necessarily names the old key to recognize and rewrite/refuse it.
      This is the opposite failure mode from the one this guard exists
      to catch — #138 was the typo silently *reappearing as the active
      read path*; #281's references are inert string literals used only
      to detect and migrate away from that old shape at the door
      (PARSE-AT-THE-DOOR), never a fallback read.
    - ``scripts/systemd/migrate_env_keys.sh``,
      ``scripts/windows/MigrateEnvKeys.ps1``,
      ``tests/unit/test_install_sh_dedupe.py``, and
      ``tests/unit/test_install_ps1_dedupe.py`` are allowlisted for issue
      #285: the migration+dedupe logic extracted from the two installers
      (and its tests) — same inert-migration-literal rationale as the
      installers above.
"""

from __future__ import annotations

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

    result = resolve_agent_token(env_path=tmp_path / "agent.env")

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
    env_path_local = tmp_path / "agent.env"
    assert not env_path_local.exists()

    result = resolve_agent_token(env_path=env_path_local, allow_generate=False)

    assert result is None


def test_resolve_prefers_new_when_both_set(monkeypatch, tmp_path):
    """Belt-and-suspenders: if a future dual-read sneaks in, the new name wins."""
    from llauncher.agent.auth import resolve_agent_token

    monkeypatch.setenv("LAUNCHER_AGENT_TOKEN", "stale")
    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "fresh")

    result = resolve_agent_token(env_path=tmp_path / "agent.env")

    assert result == "fresh"


def test_resolve_stdin_trigger_uses_new_name(monkeypatch, tmp_path):
    """``LLAUNCHER_AGENT_TOKEN=-`` triggers the stdin read path."""
    from llauncher.agent import auth as auth_mod

    monkeypatch.setenv("LLAUNCHER_AGENT_TOKEN", "-")
    monkeypatch.delenv("LAUNCHER_AGENT_TOKEN", raising=False)
    monkeypatch.setattr(auth_mod.sys, "stdin", io.StringIO("piped-token\n"))

    result = auth_mod.resolve_agent_token(env_path=tmp_path / "agent.env")

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
    env_path_local = tmp_path / "agent.env"
    assert not env_path_local.exists()

    result = resolve_agent_token(env_path=env_path_local, allow_generate=False)

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
# the legacy pattern under ``\bLAUNCHER_AGENT_``. Belt-and-suspenders:
# this file is also in ``ALLOWED_PATH_PREFIXES`` below.
_LEGACY_PREFIX = "LAUNCHER" + "_AGENT_"

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
    # #281: pre-#139 legacy-key migration (installers) and detection
    # (agent-side fail-loud guard) — see module docstring rationale.
    "scripts/windows/install.ps1",
    "scripts/systemd/install.sh",
    "llauncher/core/agent_token.py",
    "llauncher/agent/server.py",
    "tests/integration/test_agent_security_c1_c2.py",
    "tests/unit/test_agent_token_legacy_env.py",
    "docs/operations/run-as-a-service.md",
    # #285: the migration+dedupe logic extracted from the two installers so
    # it is unit-testable in isolation — same legitimate migration-code home
    # for the legacy prefix as the installers above.
    "scripts/systemd/migrate_env_keys.sh",
    "scripts/windows/MigrateEnvKeys.ps1",
    "tests/unit/test_install_sh_dedupe.py",
    "tests/unit/test_install_ps1_dedupe.py",
    # #293: rewrite-in-place persist and the duplicate-token startup guard —
    # their tests pre-seed legacy/collision shapes to pin the fix.
    "tests/unit/test_agent_token_generate.py",
    "tests/unit/test_agent_duplicate_token_guard.py",
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

    # Word-boundary regex: matches LAUNCHER_AGENT_ only when not
    # preceded by a word character. That excludes the correct
    # LLAUNCHER_AGENT_ form (where the preceding char is an L).
    pattern = r"\b" + _LEGACY_PREFIX

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
            "(see issue #138 — the rename to LLAUNCHER_AGENT_* must "
            "not be undone). Offending occurrences:\n  " + joined
        )
