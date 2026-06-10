"""Regression tests for ADR-013 logs lifecycle (issue #52).

These pin down the specific behaviors the feature introduced — and which
would silently break if someone refactored the rotation/banner ordering
or stopped reading the configured log directory at process boot:

* ``LLAUNCHER_LOG_DIR`` environment variable is honored when the settings
  module is (re)imported. Historical bug shape: a hard-coded ``LOG_DIR``
  ignored the env, so volume-mounted container deployments wrote inside
  the container instead of onto the host mount.
* The startup banner is *flushed before* ``subprocess.Popen`` inherits
  the file descriptor. Historical bug shape: the banner was written but
  not flushed, so child output landed in the file before the banner,
  defeating the "grep ``=== started at`` for run boundaries" UX promise.
* Rotation runs *before* the banner write. Historical bug shape: if the
  rotation step were moved after the open-for-append, an oversized log
  would receive the banner first and rotate on the *next* run, defeating
  the size cap by exactly one run's worth of output.

The bounded-tail and append-preserves-prior-content guarantees are
already nailed down by ``tests/unit/test_process.py::TestStartServerLogsLifecycle``
and ``TestTailFile``; we do not duplicate those here.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llauncher.core.process import log_stem_for, start_server
from llauncher.models.config import ModelConfig


@pytest.fixture
def minimal_config() -> ModelConfig:
    return ModelConfig.from_dict_unvalidated(
        {
            "name": "test-model",
            "model_path": "/fake/path/model.gguf",
            "n_gpu_layers": 255,
        }
    )


# ---------------------------------------------------------------------------
# LLAUNCHER_LOG_DIR env honored
# ---------------------------------------------------------------------------


def test_launcher_log_dir_env_honored_on_settings_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LLAUNCHER_LOG_DIR`` env var is read by ``settings`` at import time.

    Regression: a hard-coded default would silently override an
    operator-supplied env on a container with a volume mount.
    """
    custom = tmp_path / "container-mounted-logs"
    monkeypatch.setenv("LLAUNCHER_LOG_DIR", str(custom))

    import llauncher.core.settings as settings_mod

    reloaded = importlib.reload(settings_mod)
    try:
        assert reloaded.LLAUNCHER_LOG_DIR == custom
    finally:
        # Restore the un-monkey-patched module state for subsequent tests.
        monkeypatch.delenv("LLAUNCHER_LOG_DIR", raising=False)
        importlib.reload(settings_mod)


def test_launcher_log_max_bytes_env_honored_on_settings_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LLAUNCHER_LOG_MAX_BYTES`` env var is parsed as int at import time."""
    monkeypatch.setenv("LLAUNCHER_LOG_MAX_BYTES", "12345")

    import llauncher.core.settings as settings_mod

    reloaded = importlib.reload(settings_mod)
    try:
        assert reloaded.LLAUNCHER_LOG_MAX_BYTES == 12345
    finally:
        monkeypatch.delenv("LLAUNCHER_LOG_MAX_BYTES", raising=False)
        importlib.reload(settings_mod)


def test_launcher_log_keep_env_honored_on_settings_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LLAUNCHER_LOG_KEEP`` env var is parsed as int at import time."""
    monkeypatch.setenv("LLAUNCHER_LOG_KEEP", "7")

    import llauncher.core.settings as settings_mod

    reloaded = importlib.reload(settings_mod)
    try:
        assert reloaded.LLAUNCHER_LOG_KEEP == 7
    finally:
        monkeypatch.delenv("LLAUNCHER_LOG_KEEP", raising=False)
        importlib.reload(settings_mod)


# ---------------------------------------------------------------------------
# Banner flushed before subprocess inherits FD
# ---------------------------------------------------------------------------


def test_banner_flushed_before_subprocess_spawn(
    tmp_path: Path, minimal_config: ModelConfig
) -> None:
    """``log.flush()`` MUST be called before ``Popen`` inherits the fd.

    Regression: without the flush, child output races the banner buffer
    and lands first, defeating ADR-013's run-boundary grep contract.
    """
    mock_bin = MagicMock()
    mock_bin.exists.return_value = True
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Capture the order of file writes vs subprocess.Popen invocation by
    # recording each via a shared timeline list.
    timeline: list[str] = []

    real_open = open

    def tracking_open(path, mode="r", *args, **kwargs):
        # Only instrument the live log file; let other opens (settings,
        # imports, etc.) pass through untouched.
        if str(path).endswith(".log") and "a" in mode:
            f = real_open(path, mode, *args, **kwargs)
            real_flush = f.flush

            def spy_flush() -> None:
                timeline.append("flush")
                real_flush()

            f.flush = spy_flush  # type: ignore[assignment]
            return f
        return real_open(path, mode, *args, **kwargs)

    def fake_popen(*_args, **_kwargs) -> MagicMock:
        timeline.append("popen")
        return MagicMock()

    with patch("llauncher.core.process.DEFAULT_SERVER_BINARY", mock_bin), \
         patch("llauncher.core.process.LOG_DIR", log_dir), \
         patch("builtins.open", side_effect=tracking_open), \
         patch("subprocess.Popen", side_effect=fake_popen):
        start_server(minimal_config, port=8081)

    # Both events captured.
    assert "flush" in timeline, "log file was never flushed before Popen"
    assert "popen" in timeline, "Popen was never invoked"
    # Flush precedes Popen — the load-bearing ordering.
    assert timeline.index("flush") < timeline.index("popen"), (
        f"banner not flushed before subprocess spawn; timeline={timeline}"
    )


# ---------------------------------------------------------------------------
# Rotation occurs BEFORE banner write (size cap is honored on the *next*
# run, not delayed by one)
# ---------------------------------------------------------------------------


def test_rotation_runs_before_banner_write(
    tmp_path: Path, minimal_config: ModelConfig
) -> None:
    """Rotation MUST be evaluated before the banner is appended.

    Regression: if rotation moved after the open-for-append, a file
    already over the cap would absorb a full run's output before the
    *next* startup actually rotates it — defeating the cap by one run.
    """
    mock_bin = MagicMock()
    mock_bin.exists.return_value = True
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / f"{log_stem_for('test-model')}-8081.log"
    # Pre-existing oversized content.
    log_file.write_text("X" * 500)

    call_order: list[str] = []

    real_rotate = importlib.import_module(
        "llauncher.core.log_rotation"
    ).rotate_if_needed

    def tracking_rotate(path, *, max_bytes, keep):
        call_order.append("rotate")
        return real_rotate(path, max_bytes=max_bytes, keep=keep)

    real_open = open

    def tracking_open(path, mode="r", *args, **kwargs):
        if str(path).endswith(".log") and "a" in mode:
            call_order.append("open-append")
        return real_open(path, mode, *args, **kwargs)

    with patch("llauncher.core.process.DEFAULT_SERVER_BINARY", mock_bin), \
         patch("llauncher.core.process.LOG_DIR", log_dir), \
         patch("llauncher.core.process.settings.LLAUNCHER_LOG_MAX_BYTES", 100), \
         patch("llauncher.core.process.settings.LLAUNCHER_LOG_KEEP", 3), \
         patch("llauncher.core.process.log_rotation.rotate_if_needed",
               side_effect=tracking_rotate), \
         patch("builtins.open", side_effect=tracking_open), \
         patch("subprocess.Popen", return_value=MagicMock()):
        start_server(minimal_config, port=8081)

    assert "rotate" in call_order and "open-append" in call_order
    assert call_order.index("rotate") < call_order.index("open-append"), (
        f"rotation must precede banner write; order={call_order}"
    )
    # Rotation actually happened (file is .log.1 now).
    assert (log_dir / f"{log_stem_for('test-model')}-8081.log.1").exists()
