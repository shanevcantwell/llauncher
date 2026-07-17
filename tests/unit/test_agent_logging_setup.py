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


def _reset_uvicorn_loggers():
    """Restore uvicorn's loggers to a clean slate after dictConfig-ing them
    via ``_build_uvicorn_log_config`` in a test."""
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.propagate = True


class TestBuildUvicornLogConfig:
    """Regression coverage for the verified defect: ``_configure_logging``
    attaches a FileHandler to the *root* logger, but uvicorn's own
    ``uvicorn`` / ``uvicorn.access`` loggers are configured with
    ``propagate: False`` and stream-only handlers (see the stock
    ``UVICORN_LOG_CONFIG`` template), so none of uvicorn's request/access/
    error traffic ever reached ``agent.log`` — only the three startup
    lines this module logs directly did. ``_build_uvicorn_log_config``
    must inject file handlers directly onto uvicorn's loggers so this
    traffic lands in the file too.

    A test that only re-checks the root logger's handlers (as the
    pre-existing tests in this file do) would NOT have caught this: the
    root logger's FileHandler was never the problem, uvicorn's
    ``propagate: False`` loggers bypassing it was.
    """

    def test_returned_config_is_not_the_module_level_template(self, tmp_path):
        """Must be a fresh dict per call, not the shared module constant —
        mutating a per-call copy must never leak into the next call, and
        the module-level ``UVICORN_LOG_CONFIG`` must stay a pure in-memory
        template with no filesystem path baked into it at import time."""
        config = agent_server._build_uvicorn_log_config(tmp_path)
        assert config is not agent_server.UVICORN_LOG_CONFIG
        assert "filename" not in str(agent_server.UVICORN_LOG_CONFIG)

    def test_uvicorn_logger_handlers_include_a_file_handler_for_agent_log(
        self, tmp_path
    ):
        """The 'uvicorn' logger (which 'uvicorn.error' propagates into)
        must carry a FileHandler entry targeting LAUNCHER_LOG_DIR/agent.log,
        alongside — not instead of — its existing stream handler."""
        config = agent_server._build_uvicorn_log_config(tmp_path)
        handler_names = config["loggers"]["uvicorn"]["handlers"]

        file_handler_names = [
            name
            for name in handler_names
            if config["handlers"][name]["class"] == "logging.FileHandler"
        ]
        assert file_handler_names, (
            "uvicorn logger has no FileHandler — uvicorn.error traffic "
            "would only reach stdout/stderr, not agent.log"
        )
        for name in file_handler_names:
            assert config["handlers"][name]["filename"] == str(
                tmp_path / "agent.log"
            )

        stream_handler_names = [
            name
            for name in handler_names
            if config["handlers"][name]["class"] == "logging.StreamHandler"
        ]
        assert stream_handler_names, "stdout/stderr stream handler was removed"

    def test_uvicorn_access_logger_handlers_include_a_file_handler(self, tmp_path):
        """Same guarantee for 'uvicorn.access' (request logging)."""
        config = agent_server._build_uvicorn_log_config(tmp_path)
        handler_names = config["loggers"]["uvicorn.access"]["handlers"]

        file_handler_names = [
            name
            for name in handler_names
            if config["handlers"][name]["class"] == "logging.FileHandler"
        ]
        assert file_handler_names, (
            "uvicorn.access logger has no FileHandler — request logging "
            "would only reach stdout, not agent.log"
        )
        for name in file_handler_names:
            assert config["handlers"][name]["filename"] == str(
                tmp_path / "agent.log"
            )

        stream_handler_names = [
            name
            for name in handler_names
            if config["handlers"][name]["class"] == "logging.StreamHandler"
        ]
        assert stream_handler_names, "stdout stream handler was removed"

    def test_access_and_error_records_actually_land_in_agent_log(self, tmp_path):
        """Integration-style: dictConfig the built config, log through both
        ``uvicorn.access`` and ``uvicorn.error``, and assert the bytes
        actually landed in agent.log — not just that the dict *looks*
        right. This is the assertion that would have caught the live
        defect: before the fix, agent.log received only the lifecycle
        lines _configure_logging itself wrote, never uvicorn's traffic.
        """
        import logging.config

        config = agent_server._build_uvicorn_log_config(tmp_path)
        try:
            logging.config.dictConfig(config)

            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d',
                "127.0.0.1:64557",
                "GET",
                "/health",
                "1.1",
                200,
            )
            logging.getLogger("uvicorn.error").info("Application startup complete.")

            # Flush/close so the file's contents are fully written and
            # readable cross-platform (Windows keeps file handles locked
            # for writers otherwise).
            for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
                for handler in logging.getLogger(name).handlers:
                    handler.flush()

            log_file = tmp_path / "agent.log"
            assert log_file.exists()
            contents = log_file.read_text(encoding="utf-8")
            assert "/health" in contents
            assert "Application startup complete." in contents
        finally:
            _reset_uvicorn_loggers()

    def test_configure_logging_root_file_handler_and_uvicorn_file_handlers_target_same_file(
        self, monkeypatch, tmp_path
    ):
        """Belt-and-suspenders: the root logger's FileHandler (from
        ``_configure_logging``) and the uvicorn loggers' FileHandlers
        (from ``_build_uvicorn_log_config``) must resolve to the exact
        same path, so an operator tailing one file sees both lifecycle
        lines and request traffic."""
        monkeypatch.setattr(agent_server, "LAUNCHER_LOG_DIR", tmp_path)
        try:
            agent_server._configure_logging()
            root_file_handlers = [
                h
                for h in logging.getLogger().handlers
                if isinstance(h, logging.FileHandler)
            ]
            assert len(root_file_handlers) == 1
            root_path = root_file_handlers[0].baseFilename

            uvicorn_config = agent_server._build_uvicorn_log_config(tmp_path)
            uvicorn_file_paths = {
                handler["filename"]
                for handler in uvicorn_config["handlers"].values()
                if handler["class"] == "logging.FileHandler"
            }
            assert uvicorn_file_paths == {root_path}
        finally:
            _reset_root_logger()
