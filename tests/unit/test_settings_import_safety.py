"""Import-safety tests for the LLAMA_SERVER_PATH probe (issue #195).

``llauncher.core.settings`` resolves ``LLAMA_SERVER_PATH`` at module
import time, auto-detecting the ``llama-server`` binary when the
configured path is a directory. A stale / unreadable / migrated path
must **never** raise at import — an ``os.stat`` ``PermissionError`` or
``FileNotFoundError`` during ``import llauncher`` would brick the entire
package (CLI, agent, test collection all fail to load). The accessibility
of an external runtime dependency should not gate *importing the library*;
the failure must surface at point-of-use (start/preflight) instead.

These tests use ``importlib.reload`` under a patched environment to
re-execute the module body, mirroring ``tests/unit/test_state_dir.py``.
A teardown fixture reloads the module back to the ambient environment so
reloads here do not leak module state into the rest of the suite.
"""

import importlib
from pathlib import Path

import pytest

from llauncher.core import settings as settings_mod


@pytest.fixture(autouse=True)
def _restore_settings():
    """Reload settings back to the ambient env after each test."""
    yield
    importlib.reload(settings_mod)


def test_reload_does_not_raise_when_is_dir_probe_errors(monkeypatch, tmp_path):
    """A PermissionError from the ``.is_dir()`` probe must not propagate.

    This is the issue #195 repro: running as a user who cannot stat the
    configured ``LLAMA_SERVER_PATH``. The reload must succeed and the path
    must fall back to the configured value verbatim.
    """
    configured = tmp_path / "unreadable" / "llama-server"
    monkeypatch.setenv("LLAMA_SERVER_PATH", str(configured))

    real_is_dir = Path.is_dir

    def boom_is_dir(self):
        if self == configured:
            raise PermissionError(13, "Permission denied")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", boom_is_dir)

    # Must not raise.
    importlib.reload(settings_mod)

    assert settings_mod.LLAMA_SERVER_PATH == configured


def test_reload_does_not_raise_when_exists_probe_errors(monkeypatch, tmp_path):
    """A PermissionError from the per-candidate ``.exists()`` probe is tolerated.

    The configured path *is* a readable directory, but stat'ing the
    candidate binaries inside it raises. The resolver swallows the per
    candidate error and falls back to the directory path.
    """
    configured = tmp_path / "bindir"
    configured.mkdir()
    monkeypatch.setenv("LLAMA_SERVER_PATH", str(configured))

    real_exists = Path.exists

    def boom_exists(self):
        if self.parent == configured:
            raise PermissionError(13, "Permission denied")
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", boom_exists)

    importlib.reload(settings_mod)

    assert settings_mod.LLAMA_SERVER_PATH == configured


def test_directory_autodetects_binary(monkeypatch, tmp_path):
    """Happy path: a directory containing ``llama-server`` auto-detects it."""
    bindir = tmp_path / "bindir"
    bindir.mkdir()
    binary = bindir / "llama-server"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("LLAMA_SERVER_PATH", str(bindir))

    importlib.reload(settings_mod)

    assert settings_mod.LLAMA_SERVER_PATH == binary


def test_file_path_passes_through_unchanged(monkeypatch, tmp_path):
    """A plain file path (not a directory) passes through verbatim."""
    binary = tmp_path / "custom-llama-server"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setenv("LLAMA_SERVER_PATH", str(binary))

    importlib.reload(settings_mod)

    assert settings_mod.LLAMA_SERVER_PATH == binary


def test_nonexistent_path_passes_through_unchanged(monkeypatch, tmp_path):
    """A nonexistent path resolves to itself (failure deferred to use)."""
    missing = tmp_path / "does" / "not" / "exist" / "llama-server"
    monkeypatch.setenv("LLAMA_SERVER_PATH", str(missing))

    importlib.reload(settings_mod)

    assert settings_mod.LLAMA_SERVER_PATH == missing
