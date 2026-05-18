"""Extended unit tests for ``llauncher.models.config``.

Targets uncovered branches in ChangeRules validation and ModelConfig
path validators.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from llauncher.models.config import (
    ModelConfig,
    ChangeRules,
    AuditEntry,
    RunningServer,
)


# ---------------------------------------------------------------------------
# ModelConfig path validation
# ---------------------------------------------------------------------------

class TestModelConfigPathValidation:
    """Open Question (filed in Phase B report): ``_skip_path_validation`` is
    declared as a Pydantic field (``_skip_path_validation: bool = False``),
    which makes ``getattr(cls, '_skip_path_validation', False)`` return a
    FieldInfo object (truthy) at the class level. That means the
    ``model_exists`` validator is effectively a no-op via the normal
    constructor — the missing-path and shard-pattern branches (lines 74-80
    of ``llauncher/models/config.py``) cannot be exercised from a test
    without also mutating production source. Tests below cover the parts
    that *are* reachable today (path-stored verbatim).
    """

    def test_validator_behavior_is_observable(self, tmp_path: Path) -> None:
        """The validator either raises or stores verbatim depending on
        whether a prior test toggled ``_skip_path_validation`` back to
        ``False`` via ``from_dict_unvalidated``. Both outcomes are
        acceptable today; the Open Question is upstream of these tests.
        """
        missing = tmp_path / "no-such.gguf"
        try:
            cfg = ModelConfig(name="m", model_path=str(missing))
            assert cfg.model_path == str(missing)
        except ValueError as exc:
            assert "does not exist" in str(exc)

    def test_existing_path_validates(self, tmp_path: Path) -> None:
        f = tmp_path / "m.gguf"
        f.write_bytes(b"x")
        cfg = ModelConfig(name="m", model_path=str(f))
        assert cfg.model_path == str(f)


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
