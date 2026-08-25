"""Unit tests for ``operations.validate_models`` (issue #475, ADR-027).

Profiled to the ratified behavior: read-only, no audit entry, per-model
verdicts, shard resolution agreement, VRAM advisory-and-skipped-when-running,
stale-lockfile advisory.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from llauncher.core.config import ConfigStore
from llauncher.models.config import ModelConfig, _skip_path_validation

# These tests exercise the real weights/gguf_magic verdicts against real
# temp files, so they opt out of the suite-wide autouse mock that forces
# check_model_health() to always report valid (see conftest.py
# ``_patch_model_health`` — same discipline as
# ``test_agent_models_health_api`` / ``test_adr_cross_cutting``).
pytestmark = pytest.mark.real_model_health


def _write_model(name: str, path: str) -> ModelConfig:
    """Build a ``ModelConfig`` without triggering path-existence validation."""
    with _skip_path_validation():
        return ModelConfig(name=name, model_path=path)


def _gguf_bytes(magic: bool = True, size: int = 1024 * 1024 + 1) -> bytes:
    header = b"GGUF" if magic else b"NOPE"
    return header + b"\x00" * (size - len(header))


@pytest.fixture
def registry(tmp_path):
    """A config with: one present valid GGUF, one missing, one sharded."""
    present = tmp_path / "present.gguf"
    present.write_bytes(_gguf_bytes())

    # Sharded: base file present under the shard-fallback name, first-shard
    # filename absent (mirrors resolve_shard_path's derivation).
    shard_base = tmp_path / "sharded-00001.gguf"
    shard_base.write_bytes(_gguf_bytes())
    shard_configured = str(tmp_path / "sharded-00001-of-00003.gguf")

    missing_path = str(tmp_path / "does-not-exist.gguf")

    models = {
        "present-model": _write_model("present-model", str(present)),
        "missing-model": _write_model("missing-model", missing_path),
        "sharded-model": _write_model("sharded-model", shard_configured),
    }
    ConfigStore.save(models)
    return {
        "present": str(present),
        "missing": missing_path,
        "shard_base": str(shard_base),
        "shard_configured": shard_configured,
    }


def _read_audit_lines(audit_path):
    if not audit_path.exists():
        return []
    return audit_path.read_text(encoding="utf-8").splitlines()


@pytest.fixture(autouse=True)
def _no_vram_shellout():
    """Stub the VRAM check so tests don't depend on real GPU/nvidia-smi state."""
    with patch(
        "llauncher.operations.preflight.default_vram_check",
        return_value=(True, ""),
    ) as mocked:
        yield mocked


class TestValidateModelsShape:
    def test_report_shape_and_verdicts(self, registry):
        from llauncher import operations as ops

        report = ops.validate_models()

        assert report.ok is False  # missing-model fails
        names = {m.name: m for m in report.models}
        assert set(names) == {"present-model", "missing-model", "sharded-model"}

        present = names["present-model"]
        assert present.exists is True
        assert present.ok is True
        checks = {v.check for v in present.verdicts}
        assert "weights" in checks
        assert "gguf_magic" in checks

        missing = names["missing-model"]
        assert missing.exists is False
        assert missing.ok is False
        weights_verdict = next(v for v in missing.verdicts if v.check == "weights")
        assert weights_verdict.ok is False

        sharded = names["sharded-model"]
        assert sharded.exists is True, "shard fallback must resolve (issue #475 precondition)"
        assert sharded.ok is True

    def test_names_filter_selects_subset(self, registry):
        from llauncher import operations as ops

        report = ops.validate_models(names=["present-model"])
        assert len(report.models) == 1
        assert report.models[0].name == "present-model"

    def test_unknown_name_silently_skipped(self, registry):
        from llauncher import operations as ops

        report = ops.validate_models(names=["present-model", "no-such-model"])
        assert len(report.models) == 1

    def test_bad_gguf_magic_fails_gating(self, registry, tmp_path):
        from llauncher import operations as ops

        bad = tmp_path / "corrupt.gguf"
        bad.write_bytes(_gguf_bytes(magic=False))
        models = ConfigStore.load()
        models["corrupt-model"] = _write_model("corrupt-model", str(bad))
        ConfigStore.save(models)

        report = ops.validate_models(names=["corrupt-model"])
        entry = report.models[0]
        assert entry.ok is False
        magic_verdict = next(v for v in entry.verdicts if v.check == "gguf_magic")
        assert magic_verdict.ok is False
        assert magic_verdict.advisory is False


class TestValidateModelsReadOnlyContract:
    def test_no_audit_entry_written(self, registry):
        """The #463 falsifier pattern: zero new audit lines from a read."""
        from llauncher import operations as ops
        from llauncher.core import settings

        audit_path = settings.LAUNCHER_AUDIT_PATH
        before = _read_audit_lines(audit_path)

        ops.validate_models()

        after = _read_audit_lines(audit_path)
        assert after == before

    def test_config_file_mtime_unchanged(self, registry):
        from llauncher import operations as ops
        from llauncher.core.config import CONFIG_PATH

        before_mtime = CONFIG_PATH.stat().st_mtime_ns
        ops.validate_models()
        after_mtime = CONFIG_PATH.stat().st_mtime_ns
        assert before_mtime == after_mtime


class TestValidateModelsVram:
    def test_vram_verdict_present_and_advisory(self, registry, _no_vram_shellout):
        from llauncher import operations as ops

        _no_vram_shellout.return_value = (False, "insufficient VRAM: need ~7168 MiB")
        report = ops.validate_models(names=["present-model"])
        entry = report.models[0]
        vram_verdict = next(v for v in entry.verdicts if v.check == "vram")
        assert vram_verdict.advisory is True
        assert vram_verdict.ok is False
        # Advisory failure does not move ok.
        assert entry.ok is True

    def test_vram_absent_when_vram_false(self, registry):
        from llauncher import operations as ops

        report = ops.validate_models(names=["present-model"], vram=False)
        entry = report.models[0]
        assert not any(v.check == "vram" for v in entry.verdicts)

    def test_vram_absent_for_running_model(self, registry, tmp_path):
        """A model with a live lockfile skips the VRAM check entirely."""
        from llauncher import operations as ops
        from llauncher.core import lockfile as lf

        with patch.object(lf, "list_lockfiles") as mocked_list, \
                patch.object(lf, "is_pid_alive", return_value=True):
            mocked_list.return_value = [
                lf.Lockfile(
                    pid=1,
                    model="present-model",
                    port=8081,
                    started_at="2026-08-25T00:00:00Z",
                    llauncher_pid=1,
                )
            ]
            report = ops.validate_models(names=["present-model"])

        entry = report.models[0]
        assert entry.running_port == 8081
        assert not any(v.check == "vram" for v in entry.verdicts)
        assert not any(v.check == "lockfile" for v in entry.verdicts)


class TestValidateModelsLockfile:
    def test_stale_lockfile_advisory_failure(self, registry):
        from llauncher import operations as ops
        from llauncher.core import lockfile as lf

        with patch.object(lf, "list_lockfiles") as mocked_list, \
                patch.object(lf, "is_pid_alive", return_value=False):
            mocked_list.return_value = [
                lf.Lockfile(
                    pid=99999,
                    model="present-model",
                    port=8081,
                    started_at="2026-08-25T00:00:00Z",
                    llauncher_pid=1,
                )
            ]
            report = ops.validate_models(names=["present-model"])

        entry = report.models[0]
        assert entry.running_port is None
        lock_verdict = next(v for v in entry.verdicts if v.check == "lockfile")
        assert lock_verdict.advisory is True
        assert lock_verdict.ok is False
        # Advisory — does not gate ok, and validate never reconciles.
        assert entry.ok is True
