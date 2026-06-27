"""Extended unit tests for ``llauncher.models.config``.

Targets uncovered branches in ChangeRules validation and ModelConfig
path validators.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import pytest

from llauncher.models.config import (
    ModelConfig,
    ChangeRules,
    AuditEntry,
    RunningServer,
    _skip_path_validation,
    _skip_path_validation_var,
)


# ---------------------------------------------------------------------------
# ModelConfig path validation
# ---------------------------------------------------------------------------

class TestModelConfigPathValidation:
    """Path-existence validator on ``ModelConfig.model_path``.

    Issue #88(a) resolved the prior order-dependency: ``_skip_path_validation``
    is now a ``ClassVar[bool]`` rather than a Pydantic field, so the validator
    runs on first construction without requiring a prior call to
    ``from_dict_unvalidated`` to prime the class attribute.
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
# Issue #88(b): skip flag is context-scoped, effective, and non-racy
# ---------------------------------------------------------------------------

class TestSkipPathValidationContext:
    """The path-validation skip flag is a context-scoped, non-racy switch.

    Pre-#88(b) the skip flag toggled a shared class attribute, which both
    (a) leaked across constructions until reset and (b) raced under
    concurrency. These tests pin the ContextVar replacement.
    """

    def test_skip_suppresses_validation_for_missing_path(self) -> None:
        """``from_dict_unvalidated`` constructs even when the path is absent.

        This is the load-bearing behavior #88 said was broken on a fresh
        process: the skip must actually suppress the existence check.
        """
        cfg = ModelConfig.from_dict_unvalidated(
            {"name": "m", "model_path": "/definitely/does/not/exist.gguf"}
        )
        assert cfg.model_path == "/definitely/does/not/exist.gguf"

    def test_skip_does_not_leak_after_from_dict_unvalidated(self) -> None:
        """The skip is scoped to the call; a later normal construct validates."""
        ModelConfig.from_dict_unvalidated(
            {"name": "m", "model_path": "/no/such/path.gguf"}
        )
        # Flag must be back to its default outside the context.
        assert _skip_path_validation_var.get() is False
        with pytest.raises(ValueError, match="does not exist"):
            ModelConfig(name="m", model_path="/still/missing.gguf")

    def test_context_manager_restores_prior_value_when_nested(self) -> None:
        """Token-based reset restores the outer value rather than forcing False."""
        with _skip_path_validation():
            assert _skip_path_validation_var.get() is True
            with _skip_path_validation():
                assert _skip_path_validation_var.get() is True
            # Inner exit restores the outer True, not the global default.
            assert _skip_path_validation_var.get() is True
        assert _skip_path_validation_var.get() is False

    def test_skip_flag_does_not_bleed_across_threads(self) -> None:
        """Concurrent constructions do not share the skip flag (no race).

        One thread holds the skip open and builds a config with a missing
        path; a second thread, started while the first is inside the skip
        context, must still see validation enforced (its own context's
        default ``False``). Pre-#88(b) the shared class attribute let the
        first thread's ``True`` leak into the second.
        """
        gate = threading.Event()
        release = threading.Event()
        other_validated = {"raised": None}

        def hold_skip_open() -> None:
            with _skip_path_validation():
                # Build with a missing path: must succeed under the skip.
                ModelConfig(name="a", model_path="/missing/a.gguf")
                gate.set()  # signal: skip is now open in this thread
                release.wait(timeout=5)  # keep the context alive

        def expect_validation() -> None:
            gate.wait(timeout=5)  # enter while the other thread's skip is open
            try:
                ModelConfig(name="b", model_path="/missing/b.gguf")
                other_validated["raised"] = False
            except ValueError:
                other_validated["raised"] = True
            finally:
                release.set()

        t1 = threading.Thread(target=hold_skip_open)
        t2 = threading.Thread(target=expect_validation)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # The second thread saw validation enforced despite the first
        # thread holding the skip open — no cross-thread bleed.
        assert other_validated["raised"] is True


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
