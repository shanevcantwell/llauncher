"""Unit tests for ``operations.validate_models`` (issue #475, ADR-027).

Profiled to the ratified behavior: read-only, no audit entry, per-model
verdicts, shard resolution agreement, VRAM advisory-and-skipped-when-running,
stale-lockfile advisory.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from llauncher.core import gpu as gpu_mod
from llauncher.core.config import ConfigStore
from llauncher.operations import preflight
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


# ---------------------------------------------------------------------------
# Review findings on PR #481
# ---------------------------------------------------------------------------

_REAL_VRAM_CHECK = preflight.default_vram_check


def _fake_gpu(free_mb: int = 999_999):
    """A ``refresh()`` replacement that records each call (one per shell-out)."""
    calls: list[int] = []

    def _refresh(self):
        calls.append(1)
        return gpu_mod.GPUHealthResult(
            backends=["nvidia"],
            devices=[gpu_mod.GPUDevice(index=0, name="fake", free_vram_mb=free_mb)],
        )

    return calls, _refresh


class TestValidateModelsGpuQueryEconomics:
    """One GPU query per call, not one per model (blocker on PR #481).

    ``GPUHealthCollector``'s TTL cache is per-instance, so building a fresh
    collector inside the per-model check meant an ``nvidia-smi`` subprocess
    per configured model -- N-per-rerun on the Models tab, which is exactly
    the shell-out economics ADR-027 refused to put on a hot path.
    """

    def test_single_gpu_query_across_all_models(self, registry):
        from llauncher import operations as ops

        calls, fake_refresh = _fake_gpu()
        with patch(
            "llauncher.operations.preflight.default_vram_check", _REAL_VRAM_CHECK
        ), patch.object(gpu_mod.GPUHealthCollector, "refresh", fake_refresh):
            report = ops.validate_models()

        assert len(report.models) == 3
        assert all(
            any(v.check == "vram" for v in m.verdicts) for m in report.models
        ), "every model still gets a vram verdict"
        assert len(calls) == 1, f"expected one GPU query for the batch, got {len(calls)}"

    def test_no_gpu_query_at_all_when_vram_false(self, registry):
        from llauncher import operations as ops

        calls, fake_refresh = _fake_gpu()
        with patch(
            "llauncher.operations.preflight.default_vram_check", _REAL_VRAM_CHECK
        ), patch.object(gpu_mod.GPUHealthCollector, "refresh", fake_refresh):
            ops.validate_models(vram=False)

        assert calls == []


class TestValidateModelsNeverRaises:
    """The docstring promises "never raises on a bad model entry"."""

    def test_vram_adapter_exception_becomes_advisory_failure(self, registry):
        from llauncher import operations as ops

        with patch(
            "llauncher.operations.preflight.default_vram_check",
            side_effect=ValueError("malformed nvidia-smi CSV"),
        ):
            report = ops.validate_models(names=["present-model"])

        entry = report.models[0]
        verdict = next(v for v in entry.verdicts if v.check == "vram")
        assert verdict.ok is False
        assert verdict.advisory is True
        assert "malformed nvidia-smi CSV" in verdict.reason
        assert entry.ok is True, "an advisory adapter crash must not gate ok"

    def test_weights_adapter_exception_becomes_gating_failure(self, registry):
        from llauncher import operations as ops

        with patch(
            "llauncher.operations.preflight.default_model_health_check",
            side_effect=OSError("wedged handle"),
        ):
            report = ops.validate_models(names=["present-model"], vram=False)

        entry = report.models[0]
        verdict = next(v for v in entry.verdicts if v.check == "weights")
        assert verdict.ok is False
        assert "wedged handle" in verdict.reason
        assert entry.ok is False
        assert entry.status == "INVALID"


class TestValidateModelsFreshness:
    """The gating verdict and the stat'd metadata must describe one moment.

    ``check_model_health`` caches for 60 s; the metadata beside it was always
    stat'd fresh. A model deleted after any recent call therefore reported
    ``exists: false`` next to ``ok: true`` -- the false verdict #468's
    "delete entries with missing weights" loop would act on.
    """

    def test_deleted_weights_flip_ok_immediately(self, registry):
        from llauncher import operations as ops

        first = ops.validate_models(names=["present-model"], vram=False)
        assert first.models[0].ok is True

        Path(registry["present"]).unlink()

        second = ops.validate_models(names=["present-model"], vram=False)
        entry = second.models[0]
        assert entry.exists is False
        assert entry.ok is False, "a cached ok beside exists=false is the #468 false verdict"
        assert entry.status == "MISSING"

    def test_restored_weights_flip_back_immediately(self, registry):
        from llauncher import operations as ops

        Path(registry["present"]).unlink()
        assert ops.validate_models(names=["present-model"], vram=False).models[0].ok is False

        Path(registry["present"]).write_bytes(_gguf_bytes())

        entry = ops.validate_models(names=["present-model"], vram=False).models[0]
        assert entry.exists is True
        assert entry.ok is True


class TestValidateModelsResolvedPath:
    def test_resolved_path_is_fully_resolved(self, registry):
        from llauncher import operations as ops

        entry = ops.validate_models(names=["present-model"], vram=False).models[0]
        assert entry.resolved_path == str(Path(registry["present"]).resolve())

    def test_symlinked_entry_resolves_to_target(self, registry, tmp_path):
        """``resolved_path`` is documented as post-symlink resolution and must
        name the same file the weights verdict read."""
        from llauncher import operations as ops

        link = tmp_path / "link.gguf"
        try:
            link.symlink_to(registry["present"])
        except (OSError, NotImplementedError):  # pragma: no cover - needs privilege
            pytest.skip("symlink creation not permitted on this host")

        models = ConfigStore.load()
        models["linked-model"] = _write_model("linked-model", str(link))
        ConfigStore.save(models)

        entry = ops.validate_models(names=["linked-model"], vram=False).models[0]
        assert entry.ok is True
        assert entry.resolved_path == str(Path(registry["present"]).resolve())


class TestValidationStatusTokens:
    """ADR-027's status vocabulary -- a gating failure is not always MISSING."""

    def test_bad_magic_reports_bad_magic_not_missing(self, registry, tmp_path):
        from llauncher import operations as ops

        bad = tmp_path / "corrupt.gguf"
        bad.write_bytes(_gguf_bytes(magic=False))
        models = ConfigStore.load()
        models["corrupt-model"] = _write_model("corrupt-model", str(bad))
        ConfigStore.save(models)

        entry = ops.validate_models(names=["corrupt-model"], vram=False).models[0]
        assert entry.exists is True
        assert entry.status == "BAD_MAGIC"

    def test_too_small_reports_too_small(self, registry, tmp_path):
        from llauncher import operations as ops

        tiny = tmp_path / "tiny.gguf"
        tiny.write_bytes(b"GGUF" + b"\x00" * 16)
        models = ConfigStore.load()
        models["tiny-model"] = _write_model("tiny-model", str(tiny))
        ConfigStore.save(models)

        entry = ops.validate_models(names=["tiny-model"], vram=False).models[0]
        assert entry.status == "TOO_SMALL"

    def test_missing_reports_missing_and_ok_reports_ok(self, registry):
        from llauncher import operations as ops

        report = ops.validate_models(vram=False)
        names = {m.name: m for m in report.models}
        assert names["missing-model"].status == "MISSING"
        assert names["present-model"].status == "OK"

    def test_advisory_failures_surface_as_their_own_tokens(self, registry):
        from llauncher import operations as ops
        from llauncher.core import lockfile as lf

        with patch(
            "llauncher.operations.preflight.default_vram_check",
            return_value=(False, "insufficient VRAM"),
        ):
            entry = ops.validate_models(names=["present-model"]).models[0]
        assert entry.ok is True
        assert entry.status == "VRAM?"

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
            entry = ops.validate_models(names=["present-model"], vram=False).models[0]
        assert entry.ok is True
        assert entry.status == "STALE_LOCK"
