"""Extended unit tests for ``llauncher.models.config``.

Targets uncovered branches in ChangeRules validation and ModelConfig
path validators.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import pytest

from llauncher.models.config import (
    ModelConfig,
    ChangeRules,
    AuditEntry,
    RunningServer,
    _path_validation_disabled,
)


# ---------------------------------------------------------------------------
# ModelConfig path validation
# ---------------------------------------------------------------------------

class TestModelConfigPathValidation:
    """Path-existence validator on ``ModelConfig.model_path``.

    Issue #88 resolved the prior order-dependency: the skip flag is a
    context-local ``ContextVar`` (entered via ``_path_validation_disabled``)
    rather than a Pydantic private attr or mutable class attribute, so the
    validator runs on first construction in a fresh process and concurrent
    callers cannot observe each other's skip window.
    """

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        """Validator raises on a missing path via the normal constructor."""
        missing = tmp_path / "no-such.gguf"
        with pytest.raises(ValueError, match="does not exist"):
            ModelConfig(name="m", model_path=str(missing))

    def test_existing_path_validates(self, tmp_path: Path) -> None:
        f = tmp_path / "m.gguf"
        f.write_bytes(b"x")
        cfg = ModelConfig(name="m", model_path=str(f))
        assert cfg.model_path == str(f)

    def test_missing_non_shard_path_raises(self, tmp_path: Path) -> None:
        """Cover ``llauncher/models/config.py`` — the non-shard
        missing-path raise. Order-independent post #88(a).
        """
        missing = tmp_path / "does-not-exist.gguf"  # no ``-of-`` shard marker
        with pytest.raises(ValueError, match="does not exist"):
            ModelConfig(name="m", model_path=str(missing))

    def test_fresh_process_validates_missing_path(self, tmp_path: Path) -> None:
        """Regression for issue #88(a): on a *fresh* interpreter — no prior
        ``from_dict_unvalidated`` call in the process — constructing with a
        missing ``model_path`` must raise.

        Run in a subprocess because the bug was order-dependent: the old
        PrivateAttr descriptor was truthy at the class level, so the very
        first construction in a process silently skipped validation. In-suite
        tests cannot prove the fresh-process behavior.
        """
        missing = tmp_path / "no-such-fresh.gguf"
        script = (
            "from llauncher.models.config import ModelConfig\n"
            "try:\n"
            f"    ModelConfig(name='m', model_path={str(missing)!r})\n"
            "except ValueError as e:\n"
            "    assert 'does not exist' in str(e), str(e)\n"
            "else:\n"
            "    raise SystemExit('validator silently skipped on fresh process')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_skip_window_is_isolated_across_threads(self, tmp_path: Path) -> None:
        """Issue #88(b): the skip flag is context-local, so another thread's
        open skip window must not disable validation here.

        Behavioral (not a stress test): the ``ContextVar`` mechanism makes
        the old class-attribute race structurally impossible, so it suffices
        to hold a skip window open in one thread and assert the main thread
        still validates.
        """
        window_open = threading.Event()
        release = threading.Event()
        skipped_cfg: list[ModelConfig] = []

        def hold_skip_window() -> None:
            with _path_validation_disabled():
                # Inside the window this thread may construct with a
                # nonexistent path...
                skipped_cfg.append(
                    ModelConfig(name="m", model_path="/fake/elsewhere.gguf")
                )
                window_open.set()
                release.wait(timeout=10)

        t = threading.Thread(target=hold_skip_window)
        t.start()
        try:
            assert window_open.wait(timeout=10)
            # ...while the main thread, concurrently, still validates.
            missing = tmp_path / "missing-while-window-open.gguf"
            with pytest.raises(ValueError, match="does not exist"):
                ModelConfig(name="m", model_path=str(missing))
        finally:
            release.set()
            t.join(timeout=10)
        assert skipped_cfg and skipped_cfg[0].model_path == "/fake/elsewhere.gguf"

    def test_skip_window_restored_after_exception(self) -> None:
        """The skip flag resets even when validation fails inside the window
        (e.g. ``from_dict_unvalidated`` on data failing a *different*
        validator), so later constructions validate normally."""
        with pytest.raises(ValueError, match="llauncher-managed flag"):
            ModelConfig.from_dict_unvalidated({
                "name": "m",
                "model_path": "/fake/path.gguf",
                "extra_args": "--api-key sneaky",
            })
        with pytest.raises(ValueError, match="does not exist"):
            ModelConfig(name="m", model_path="/fake/still-validated.gguf")


# ---------------------------------------------------------------------------
# from_dict / unvalidated migrations
# ---------------------------------------------------------------------------

class TestFromDictUnvalidated:
    def test_legacy_extra_args_list_is_joined(self) -> None:
        cfg = ModelConfig.from_dict_unvalidated({
            "name": "m",
            "model_path": "/fake/path.gguf",
            "extra_args": ["--foo", "bar", "--baz"],
        })
        assert cfg.extra_args == "--foo bar --baz"

    def test_legacy_port_fields_silently_dropped(self) -> None:
        cfg = ModelConfig.from_dict_unvalidated({
            "name": "m",
            "model_path": "/fake/path.gguf",
            "default_port": 8081,
            "port": 8082,
            "host": "0.0.0.0",
        })
        assert cfg.name == "m"
        assert not hasattr(cfg, "default_port")

    def test_from_dict_with_existing_path(self, tmp_path: Path) -> None:
        f = tmp_path / "m.gguf"
        f.write_bytes(b"x")
        cfg = ModelConfig.from_dict({"name": "m", "model_path": str(f)})
        assert cfg.name == "m"


# ---------------------------------------------------------------------------
# ChangeRules validate_start branches
# ---------------------------------------------------------------------------

class TestChangeRulesValidation:
    def _cfg(self, name: str = "test-model") -> ModelConfig:
        return ModelConfig.from_dict_unvalidated({
            "name": name,
            "model_path": "/fake/m.gguf",
        })

    def test_blacklisted_port_blocked(self) -> None:
        rules = ChangeRules(blacklisted_ports={9999})
        ok, msg = rules.validate_start(self._cfg(), "cli", 9999)
        assert ok is False
        assert "blacklisted" in msg.lower()

    def test_blacklisted_caller_blocked(self) -> None:
        rules = ChangeRules(blacklisted_callers={"hacker"})
        ok, msg = rules.validate_start(self._cfg(), "hacker", 8081)
        assert ok is False
        assert "hacker" in msg

    def test_non_whitelisted_model_blocked(self) -> None:
        rules = ChangeRules(whitelisted_models={"allowed-only"})
        ok, msg = rules.validate_start(self._cfg("other"), "cli", 8081)
        assert ok is False
        assert "whitelisted" in msg

    def test_whitelisted_model_allowed(self) -> None:
        rules = ChangeRules(whitelisted_models={"test-model"})
        ok, msg = rules.validate_start(self._cfg(), "cli", 8081)
        assert ok is True

    def test_validate_stop_blacklisted_caller(self) -> None:
        rules = ChangeRules(blacklisted_callers={"bad"})
        ok, msg = rules.validate_stop(8081, "bad")
        assert ok is False

    def test_validate_stop_default_ok(self) -> None:
        rules = ChangeRules()
        ok, _msg = rules.validate_stop(8081, "cli")
        assert ok is True


# ---------------------------------------------------------------------------
# RunningServer & AuditEntry serialization
# ---------------------------------------------------------------------------

class TestRunningServerToDict:
    def test_to_dict_keys(self) -> None:
        rs = RunningServer(
            pid=42,
            port=8081,
            config_name="m",
            start_time=datetime.now(),
            logs_path="/tmp/x.log",
        )
        d = rs.to_dict()
        assert set(d) == {"pid", "port", "config_name", "start_time", "logs_path", "uptime_seconds"}
        assert d["pid"] == 42
        assert isinstance(d["uptime_seconds"], int)


class TestAuditEntryToDict:
    def test_to_dict_keys(self) -> None:
        e = AuditEntry(
            timestamp=datetime.now(),
            action="start",
            model="m",
            caller="cli",
            result="success",
            message="ok",
        )
        d = e.to_dict()
        assert d["action"] == "start"
        assert d["result"] == "success"
        assert d["message"] == "ok"
