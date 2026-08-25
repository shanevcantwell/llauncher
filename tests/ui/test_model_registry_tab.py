"""Streamlit ``AppTest`` tests for the Model Registry tab
(``llauncher/ui/tabs/model_registry.py``).

Contract pinned here (issue #475, ADR-027): for ``target == "local"`` the
tab calls ``operations.validate_models()`` directly (no ``core.model_health``
import — that fork is exactly what ADR-027 closed); for a remote target it
calls ``aggregator.get_validation(target)``, never local state. Each row's
status column is derived from the entry's own ``verdicts``/``ok``, not a
second copy of the rule:

* ``ok=False``                                    -> "❌ missing (<gating reasons>)"
* ``ok=True`` with an advisory verdict failure     -> "⚠️ ready (<advisory reasons>)"
* ``ok=True`` with no advisory failures            -> "✅ ready"

Also pinned: the empty-state branches (no models locally; no aggregator
report for a remote target) and that the remote-target path never touches
local state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from llauncher.models.config import ModelConfig
from llauncher.models.validation import ModelValidation, ValidationReport, ValidationVerdict
from llauncher.ui.tabs.model_registry import render_model_registry


def _make_model(name="test-model", path="/models/test-model.gguf"):
    return ModelConfig.from_dict_unvalidated({"name": name, "model_path": path})


def _validation(
    name="test-model",
    path="/models/test-model.gguf",
    *,
    ok=True,
    exists=True,
    size_bytes=2_000_000,
    last_modified=None,
    verdicts=None,
):
    if verdicts is None:
        verdicts = [ValidationVerdict(check="weights", ok=ok, reason="" if ok else "not found")]
    return ModelValidation(
        name=name,
        model_path=path,
        exists=exists,
        size_bytes=size_bytes,
        last_modified=last_modified,
        verdicts=verdicts,
        ok=ok,
    )


def _report(models):
    return ValidationReport(checked_at=datetime.now(timezone.utc), ok=all(m.ok for m in models), models=models)


def _status_cells(at):
    """Extract the rendered dataframe's ``status`` column values."""
    df = at.dataframe[0].value
    return list(df["status"])


class TestModelRegistryEmptyStates:
    """No-model states surface an informational banner, never a table."""

    def test_local_target_with_no_models_shows_info_banner(
        self, tab_harness, mock_state, mock_aggregator
    ):
        with patch("llauncher.operations.validate_models", return_value=_report([])):
            at = tab_harness(render_model_registry, mock_state, None, mock_aggregator, "local")

        assert not at.exception
        assert "No models configured" in at.info[0].value
        assert "local" in at.info[0].value
        assert len(at.dataframe) == 0

    def test_remote_target_with_no_aggregator_data_shows_info_banner(
        self, tab_harness, mock_state, mock_aggregator
    ):
        mock_aggregator.get_validation.return_value = None

        at = tab_harness(
            render_model_registry, mock_state, None, mock_aggregator, "gpu-rig"
        )

        assert not at.exception
        assert "No models configured" in at.info[0].value
        assert "gpu-rig" in at.info[0].value
        assert len(at.dataframe) == 0

    def test_remote_target_with_no_aggregator_at_all_shows_info_banner(
        self, tab_harness, mock_state
    ):
        """``aggregator=None`` for a remote target is treated as no data."""
        at = tab_harness(render_model_registry, mock_state, None, None, "gpu-rig")

        assert not at.exception
        assert "No models configured" in at.info[0].value
        assert len(at.dataframe) == 0


class TestModelRegistryStatusBranches:
    """Each validation outcome renders its pinned status label."""

    def test_ready_model_renders_ready_status(
        self, tab_harness, mock_state, mock_aggregator
    ):
        report = _report([_validation(ok=True)])

        with patch("llauncher.operations.validate_models", return_value=report):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        assert _status_cells(at) == ["✅ ready"]

    def test_missing_model_renders_missing_status_with_reason(
        self, tab_harness, mock_state, mock_aggregator
    ):
        report = _report([_validation(ok=False, exists=False)])

        with patch("llauncher.operations.validate_models", return_value=report):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        cells = _status_cells(at)
        assert len(cells) == 1
        assert cells[0].startswith("❌ missing")
        assert "not found" in cells[0]

    def test_advisory_failure_renders_ready_with_reason_not_missing(
        self, tab_harness, mock_state, mock_aggregator
    ):
        """An advisory-only failure (stale lockfile, low VRAM) keeps the
        badge "ready" — advisory verdicts never gate the status (ADR-027)."""
        verdicts = [
            ValidationVerdict(check="weights", ok=True),
            ValidationVerdict(
                check="lockfile", ok=False, reason="stale lockfile on port 8081", advisory=True
            ),
        ]
        report = _report([_validation(ok=True, verdicts=verdicts)])

        with patch("llauncher.operations.validate_models", return_value=report):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        cells = _status_cells(at)
        assert cells[0].startswith("⚠️ ready")
        assert "stale lockfile" in cells[0]

    def test_validate_models_call_does_not_touch_core_model_health(
        self, tab_harness, mock_state, mock_aggregator
    ):
        """Regression guard: the tab must not import ``core.model_health``
        directly (ADR-027 closes the forked-vocabulary defect)."""
        report = _report([_validation(ok=True)])

        with patch("llauncher.operations.validate_models", return_value=report), patch(
            "llauncher.core.model_health.check_model_health"
        ) as mock_health:
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        mock_health.assert_not_called()


class TestModelRegistryRemoteTarget:
    """A remote ``target`` reads models through the aggregator facade only."""

    def test_remote_target_renders_rows_from_aggregator_not_local_state(
        self, tab_harness, mock_state, mock_aggregator
    ):
        remote_report = {
            "checked_at": "2026-08-25T00:00:00+00:00",
            "ok": True,
            "models": [
                {
                    "name": "remote-model",
                    "model_path": "/remote/models/remote-model.gguf",
                    "exists": True,
                    "size_bytes": 2_000_000,
                    "last_modified": None,
                    "verdicts": [{"check": "weights", "ok": True, "reason": "", "advisory": False}],
                    "ok": True,
                }
            ],
        }
        mock_aggregator.get_validation.return_value = remote_report

        at = tab_harness(
            render_model_registry, mock_state, None, mock_aggregator, "gpu-rig"
        )

        assert not at.exception
        mock_aggregator.get_validation.assert_called_once_with("gpu-rig")
        df = at.dataframe[0].value
        assert list(df["name"]) == ["remote-model"]
        assert list(df["node"]) == ["gpu-rig"]
        # Local target's state was never consulted for a remote render.
        mock_state.refresh.assert_not_called()


class TestModelRegistrySizeFormatting:
    """The rendered ``size`` column is a human-readable string per byte scale."""

    @pytest.mark.parametrize(
        "size_bytes, expected",
        [
            (512, "512 B"),
            (2048, "2.0 KB"),
            (5 * 1024 * 1024 * 1024, "5.00 GB"),
        ],
    )
    def test_size_column_scales_with_byte_count(
        self, tab_harness, mock_state, mock_aggregator, size_bytes, expected
    ):
        report = _report([_validation(ok=True, size_bytes=size_bytes)])

        with patch("llauncher.operations.validate_models", return_value=report):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        df = at.dataframe[0].value
        assert list(df["size"]) == [expected]

    def test_no_size_renders_em_dash(
        self, tab_harness, mock_state, mock_aggregator
    ):
        report = _report([_validation(ok=True, size_bytes=None)])

        with patch("llauncher.operations.validate_models", return_value=report):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        df = at.dataframe[0].value
        assert list(df["size"]) == ["—"]


class TestModelRegistryLastModifiedFormatting:
    """The rendered ``last_modified`` column tolerates non-datetime values
    (#347 regression posture — a string must render as-is, never raise)."""

    def test_datetime_last_modified_renders_formatted_timestamp(
        self, tab_harness, mock_state, mock_aggregator
    ):
        when = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        report = _report([_validation(ok=True, last_modified=when)])

        with patch("llauncher.operations.validate_models", return_value=report):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        df = at.dataframe[0].value
        assert list(df["last_modified"]) == ["2026-01-02 03:04"]

    def test_iso_string_last_modified_renders_formatted_timestamp(
        self, tab_harness, mock_state, mock_aggregator
    ):
        """A remote report's ``last_modified`` arrives as a JSON ISO string
        (``ValidationReport.model_dump(mode="json")`` over HTTP)."""
        mock_aggregator.get_validation.return_value = {
            "checked_at": "2026-08-25T00:00:00+00:00",
            "ok": True,
            "models": [
                {
                    "name": "remote-model",
                    "model_path": "/remote/m.gguf",
                    "exists": True,
                    "size_bytes": None,
                    "last_modified": "2026-01-02T03:04:00+00:00",
                    "verdicts": [],
                    "ok": True,
                }
            ],
        }

        at = tab_harness(
            render_model_registry, mock_state, None, mock_aggregator, "gpu-rig"
        )

        assert not at.exception
        df = at.dataframe[0].value
        assert list(df["last_modified"]) == ["2026-01-02 03:04"]

    def test_none_last_modified_renders_em_dash(
        self, tab_harness, mock_state, mock_aggregator
    ):
        report = _report([_validation(ok=True, last_modified=None)])

        with patch("llauncher.operations.validate_models", return_value=report):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        df = at.dataframe[0].value
        assert list(df["last_modified"]) == ["—"]


class TestModelRegistryLocalTargetRefresh:
    """The local target's model list is validated after refreshing local state."""

    def test_local_target_calls_state_refresh_and_validate_models(
        self, tab_harness, mock_state, mock_aggregator
    ):
        report = _report([_validation(ok=True)])

        with patch(
            "llauncher.operations.validate_models", return_value=report
        ) as mock_validate:
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        mock_state.refresh.assert_called_once()
        mock_validate.assert_called_once_with()
