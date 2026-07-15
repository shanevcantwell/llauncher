"""Unit tests for ``llauncher.agent.server._configure_logging`` (issue #128).

Symptom: on Windows (NSSM-managed agent service), the agent's stdout/stderr
are redirected to files that NSSM captures, but Python block-buffers a
non-TTY stream — so runtime log lines sit in an 8 KB buffer indefinitely
instead of reaching the file. ``_configure_logging`` closes this two ways:

1. Reconfigures ``sys.stdout``/``sys.stderr`` to line-buffering when the
   stream supports it (``TextIOWrapper.reconfigure``, Python 3.7+).
2. Adds a ``logging.FileHandler`` targeting
   ``LAUNCHER_LOG_DIR / "agent.log"`` alongside the existing
   ``StreamHandler``, so the agent has its own durable log file
   independent of whatever the supervisor does with stdout/stderr.

These tests exercise ``_configure_logging`` directly (not via
``run_agent``) and always redirect ``LAUNCHER_LOG_DIR`` to ``tmp_path`` so
no real ``~/.llauncher`` state is touched.
"""

from __future__ import annotations

import logging

import llauncher.agent.server as agent_server


def _reset_root_logger():
    """Restore the root logger to a clean slate after mutating it via
    ``logging.basicConfig(force=True)`` in the module under test."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


def test_configure_logging_adds_file_handler_targeting_log_dir(monkeypatch, tmp_path):
    """A FileHandler pointed at LAUNCHER_LOG_DIR/agent.log is configured."""
    monkeypatch.setattr(agent_server, "LAUNCHER_LOG_DIR", tmp_path)
    try:
        agent_server._configure_logging()

        root = logging.getLogger()
        file_handlers = [
            h for h in root.handlers if isinstance(h, logging.FileHandler)
        ]
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == str(tmp_path / "agent.log")

        stream_handlers = [
            h
            for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) == 1
    finally:
        _reset_root_logger()


def test_configure_logging_creates_log_dir(monkeypatch, tmp_path):
    """The log directory is created if it does not already exist."""
    log_dir = tmp_path / "nested" / "logs"
    assert not log_dir.exists()
    monkeypatch.setattr(agent_server, "LAUNCHER_LOG_DIR", log_dir)
    try:
        agent_server._configure_logging()
        assert log_dir.is_dir()
    finally:
        _reset_root_logger()


def test_configure_logging_reconfigures_streams_when_supported(monkeypatch, tmp_path):
    """stdout/stderr are reconfigured to line-buffering when the stream
    exposes ``reconfigure`` (guarded by ``hasattr``, per the plan)."""
    monkeypatch.setattr(agent_server, "LAUNCHER_LOG_DIR", tmp_path)

    calls: list[str] = []

    class FakeStream:
        def reconfigure(self, **kwargs):
            calls.append("reconfigured")
            assert kwargs == {"line_buffering": True}

    fake_out = FakeStream()
    fake_err = FakeStream()
    monkeypatch.setattr(agent_server.sys, "stdout", fake_out)
    monkeypatch.setattr(agent_server.sys, "stderr", fake_err)

    try:
        agent_server._configure_logging()
        assert calls == ["reconfigured", "reconfigured"]
    finally:
        _reset_root_logger()


def test_configure_logging_skips_reconfigure_when_unsupported(monkeypatch, tmp_path):
    """A stream without ``reconfigure`` (e.g. a plain buffer / StringIO
    substitute, as tests already do for ``sys.stderr``) must not raise."""
    monkeypatch.setattr(agent_server, "LAUNCHER_LOG_DIR", tmp_path)

    class StreamWithoutReconfigure:
        """Deliberately has no ``reconfigure`` attribute."""

        def write(self, *_args, **_kwargs):
            pass

        def flush(self):
            pass

    monkeypatch.setattr(agent_server.sys, "stdout", StreamWithoutReconfigure())
    monkeypatch.setattr(agent_server.sys, "stderr", StreamWithoutReconfigure())

    try:
        # Must not raise.
        agent_server._configure_logging()
    finally:
        _reset_root_logger()


def test_configure_logging_preserves_format_string(monkeypatch, tmp_path):
    """The existing timestamp/name/level/message format is preserved."""
    monkeypatch.setattr(agent_server, "LAUNCHER_LOG_DIR", tmp_path)
    try:
        agent_server._configure_logging()
        root = logging.getLogger()
        for handler in root.handlers:
            assert handler.formatter is not None
            assert handler.formatter._fmt == agent_server._LOG_FORMAT
    finally:
        _reset_root_logger()
