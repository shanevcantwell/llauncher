"""Tests for the single LAUNCHER_STATE_DIR durable-state base (issue #196).

The durable-state paths (config.json, nodes.json, node_tokens.json,
agent.token, run/, audit.jsonl, logs/) all derive from one
env-configurable base, ``LAUNCHER_STATE_DIR``, defined in
``llauncher.core.settings``.

Most of the paths under test are **module-level constants resolved at
import time** (``settings.LAUNCHER_RUN_DIR``, ``config.CONFIG_DIR``,
``registry.NODES_FILE``, ...). They cannot be re-resolved by merely
setting an env var after import, so these tests use
``importlib.reload`` under a patched environment to re-execute the
module bodies. ``llauncher.agent.auth.default_token_path`` is the
exception — it resolves the base lazily at call time, so it picks up a
``monkeypatch.setattr`` on ``settings.LAUNCHER_STATE_DIR`` without a
reload.

A teardown fixture reloads the touched modules back to their default
(unpatched) environment so reloads here do not leak module state into
the rest of the suite.
"""

import importlib
from pathlib import Path

import pytest

from llauncher.agent import auth as auth_mod
from llauncher.core import config as config_mod
from llauncher.core import settings as settings_mod
from llauncher.remote import registry as registry_mod


def _reload_state_modules():
    """Re-execute settings then the modules that derive paths from it.

    Order matters: ``settings`` first (it owns ``LAUNCHER_STATE_DIR``),
    then ``config`` and ``registry`` which bind their constants from the
    freshly reloaded ``settings`` at their own import time.
    """
    importlib.reload(settings_mod)
    importlib.reload(config_mod)
    importlib.reload(registry_mod)


@pytest.fixture(autouse=True)
def _restore_state_modules():
    """Reload the state modules back to the ambient env after each test."""
    yield
    _reload_state_modules()


# --- (a) unset env -> legacy ~/.llauncher defaults ----------------------

def test_defaults_under_home_llauncher_when_unset(monkeypatch, tmp_path):
    """With no env overrides every path derives from ~/.llauncher."""
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    for var in (
        "LAUNCHER_STATE_DIR",
        "LAUNCHER_RUN_DIR",
        "LAUNCHER_AUDIT_PATH",
        "LAUNCHER_LOG_DIR",
    ):
        monkeypatch.delenv(var, raising=False)

    _reload_state_modules()

    base = fake_home / ".llauncher"
    assert settings_mod.LAUNCHER_STATE_DIR == base
    assert settings_mod.LAUNCHER_RUN_DIR == base / "run"
    assert settings_mod.LAUNCHER_AUDIT_PATH == base / "audit.jsonl"
    assert settings_mod.LAUNCHER_LOG_DIR == base / "logs"
    assert config_mod.CONFIG_DIR == base
    assert config_mod.CONFIG_PATH == base / "config.json"
    assert registry_mod.NODES_FILE == base / "nodes.json"
    assert registry_mod.NODE_TOKENS_FILE == base / "node_tokens.json"
    # auth resolves lazily at call time
    monkeypatch.setattr(settings_mod, "LAUNCHER_STATE_DIR", base)
    assert auth_mod.default_token_path() == base / "agent.token"


# --- (b) LAUNCHER_STATE_DIR redirects every derived path ----------------

def test_state_dir_redirects_all_paths(monkeypatch, tmp_path):
    """Setting LAUNCHER_STATE_DIR points every actor at the shared base."""
    base = tmp_path / "somewhere"
    monkeypatch.setenv("LAUNCHER_STATE_DIR", str(base))
    for var in ("LAUNCHER_RUN_DIR", "LAUNCHER_AUDIT_PATH", "LAUNCHER_LOG_DIR"):
        monkeypatch.delenv(var, raising=False)

    _reload_state_modules()

    assert settings_mod.LAUNCHER_STATE_DIR == base
    assert settings_mod.LAUNCHER_RUN_DIR == base / "run"
    assert settings_mod.LAUNCHER_AUDIT_PATH == base / "audit.jsonl"
    assert settings_mod.LAUNCHER_LOG_DIR == base / "logs"
    assert config_mod.CONFIG_DIR == base
    assert config_mod.CONFIG_PATH == base / "config.json"
    assert registry_mod.NODES_FILE == base / "nodes.json"
    assert registry_mod.NODE_TOKENS_FILE == base / "node_tokens.json"
    monkeypatch.setattr(settings_mod, "LAUNCHER_STATE_DIR", base)
    assert auth_mod.default_token_path() == base / "agent.token"


def test_state_dir_is_not_home_relative(monkeypatch, tmp_path):
    """An absolute non-home base is honored verbatim (multiuser deploy)."""
    base = Path("/var/lib/llauncher-shared")
    # Make home obviously different so a regression to home-relative fails.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("LAUNCHER_STATE_DIR", str(base))

    _reload_state_modules()

    assert settings_mod.LAUNCHER_STATE_DIR == base
    assert config_mod.CONFIG_PATH == base / "config.json"
    assert registry_mod.NODES_FILE == base / "nodes.json"


# --- (c) explicit per-dir override still wins ---------------------------

# Each per-dir override var, the settings attribute it controls, and the
# base-derived default the override must beat. Parameterized so every
# override seam is exercised, not just LAUNCHER_RUN_DIR.
_PER_DIR_OVERRIDES = (
    ("LAUNCHER_RUN_DIR", "LAUNCHER_RUN_DIR", "run"),
    ("LAUNCHER_AUDIT_PATH", "LAUNCHER_AUDIT_PATH", "audit.jsonl"),
    ("LAUNCHER_LOG_DIR", "LAUNCHER_LOG_DIR", "logs"),
)


@pytest.mark.parametrize(
    "override_var, settings_attr, base_suffix", _PER_DIR_OVERRIDES
)
def test_explicit_per_dir_override_wins_over_base(
    monkeypatch, tmp_path, override_var, settings_attr, base_suffix
):
    """An explicit per-dir env var beats its LAUNCHER_STATE_DIR-derived default.

    Covers each override seam (``LAUNCHER_RUN_DIR``,
    ``LAUNCHER_AUDIT_PATH``, ``LAUNCHER_LOG_DIR``): the overridden var
    takes the explicit value while every un-overridden sibling still
    derives from the base.
    """
    base = tmp_path / "base"
    override_value = tmp_path / "elsewhere" / base_suffix
    monkeypatch.setenv("LAUNCHER_STATE_DIR", str(base))
    # Clear all per-dir vars first, then set only the one under test, so
    # the siblings are guaranteed base-derived (not leaked from the env).
    for var, _, _ in _PER_DIR_OVERRIDES:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(override_var, str(override_value))

    _reload_state_modules()

    # Explicit override wins for the var under test...
    assert getattr(settings_mod, settings_attr) == override_value
    # ...while the un-overridden siblings still derive from the base.
    for var, attr, suffix in _PER_DIR_OVERRIDES:
        if var == override_var:
            continue
        assert getattr(settings_mod, attr) == base / suffix


def test_no_filesystem_touch_at_import(monkeypatch, tmp_path):
    """Reloading settings must not create the state dir on disk."""
    base = tmp_path / "never-created"
    monkeypatch.setenv("LAUNCHER_STATE_DIR", str(base))

    _reload_state_modules()

    assert not base.exists()
