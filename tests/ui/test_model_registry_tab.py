"""Streamlit ``AppTest`` tests for the Model Registry tab
(``llauncher/ui/tabs/model_registry.py``).

Contract pinned here: for a given ``target`` (``"local"`` or a remote node
name), ``render_model_registry`` gathers that target's configured models —
from ``state.models`` when local, from ``aggregator.get_all_models()`` when
remote — and renders one health-annotated row per model via
``check_model_health()`` (ADR-LLNCH-005). Each row's status column is a
deterministic function of the health result:

* ``exists=False``                              -> "❌ missing"
* ``valid=True``                                 -> "✅ ready"
* ``valid=False`` and reason mentions "too small"/"unreadable" -> "⚠️ corrupted"
* ``valid=False`` with any other reason          -> "❓ unknown (<reason>)"
* ``check_model_health`` raising                 -> treated as "❌ missing"
  (the exception-swallow branch defaults ``exists=False``)

No health data reaches the UI except through ``check_model_health`` — this
file patches that single seam (``llauncher.core.model_health.check_model_health``,
patched at its defining module because ``model_registry.py`` imports it
locally on each render) to hit every status branch deterministically, without
touching real files on disk.

Also pinned: the empty-state branches (no models configured locally; no
aggregator data for a remote target) and the remote-target data path, which
reads from the mocked ``RemoteAggregator`` facade only — never local state.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llauncher.core.model_health import ModelHealthResult
from llauncher.models.config import ModelConfig
from llauncher.ui.tabs.model_registry import render_model_registry


def _make_model(name="test-model", path="/models/test-model.gguf"):
    return ModelConfig.from_dict_unvalidated({"name": name, "model_path": path})


def _status_cells(at):
    """Extract the rendered dataframe's ``status`` column values.

    The tab renders via ``st.dataframe(df, ...)`` — AppTest exposes this as
    an ``at.dataframe`` element whose ``.value`` is the underlying
    ``pandas.DataFrame`` passed to it.
    """
    df = at.dataframe[0].value
    return list(df["status"])


class TestModelRegistryEmptyStates:
    """No-model states surface an informational banner, never a table."""

    def test_local_target_with_no_models_shows_info_banner(
        self, tab_harness, mock_state, mock_aggregator
    ):
        mock_state.models = {}

        at = tab_harness(render_model_registry, mock_state, None, mock_aggregator, "local")

        assert not at.exception
        assert "No models configured" in at.info[0].value
        assert "local" in at.info[0].value
        assert len(at.dataframe) == 0

    def test_remote_target_with_no_aggregator_data_shows_info_banner(
        self, tab_harness, mock_state, mock_aggregator
    ):
        mock_aggregator.get_all_models.return_value = {}

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


class TestModelRegistryHealthStatusBranches:
    """Each ``check_model_health`` outcome renders its pinned status label.

    ``check_model_health`` is patched inline per test (not via conftest) so
    each branch is deterministic and independent of any real file on disk.
    """

    def test_ready_model_renders_ready_status(
        self, tab_harness, mock_state, mock_aggregator
    ):
        mock_state.models = {"test-model": _make_model()}
        healthy = ModelHealthResult(valid=True, exists=True, readable=True, size_bytes=2_000_000)

        with patch(
            "llauncher.core.model_health.check_model_health", return_value=healthy
        ) as mock_check:
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        mock_check.assert_called_once_with("/models/test-model.gguf")
        assert _status_cells(at) == ["✅ ready"]

    def test_missing_model_renders_missing_status(
        self, tab_harness, mock_state, mock_aggregator
    ):
        mock_state.models = {"test-model": _make_model()}
        missing = ModelHealthResult(valid=False, exists=False, reason="not found")

        with patch(
            "llauncher.core.model_health.check_model_health", return_value=missing
        ):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        assert _status_cells(at) == ["❌ missing"]

    @pytest.mark.parametrize("reason", ["too small", "unreadable"])
    def test_corrupted_model_renders_corrupted_status(
        self, tab_harness, mock_state, mock_aggregator, reason
    ):
        mock_state.models = {"test-model": _make_model()}
        corrupted = ModelHealthResult(
            valid=False, exists=True, readable=(reason != "unreadable"), reason=reason
        )

        with patch(
            "llauncher.core.model_health.check_model_health", return_value=corrupted
        ):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        assert _status_cells(at) == ["⚠️ corrupted"]

    def test_unknown_reason_renders_unknown_status_with_reason(
        self, tab_harness, mock_state, mock_aggregator
    ):
        mock_state.models = {"test-model": _make_model()}
        odd = ModelHealthResult(valid=False, exists=True, readable=True, reason="checksum mismatch")

        with patch(
            "llauncher.core.model_health.check_model_health", return_value=odd
        ):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        cells = _status_cells(at)
        assert len(cells) == 1
        assert cells[0].startswith("❓ unknown")
        assert "checksum mismatch" in cells[0]

    def test_check_model_health_exception_is_swallowed_and_renders_missing(
        self, tab_harness, mock_state, mock_aggregator
    ):
        """A raising ``check_model_health`` must not crash the tab.

        The ``except Exception`` branch in ``render_model_registry`` defaults
        to ``valid=False`` / ``exists=False`` on any exception, which resolves
        to the same "missing" status as a health check that cleanly reports a
        nonexistent file — the row still renders, never an ``at.exception``.
        """
        mock_state.models = {"test-model": _make_model()}

        with patch(
            "llauncher.core.model_health.check_model_health",
            side_effect=RuntimeError("disk exploded"),
        ):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        assert _status_cells(at) == ["❌ missing"]


class TestModelRegistryRemoteTarget:
    """A remote ``target`` reads models through the aggregator facade only."""

    def test_remote_target_renders_rows_from_aggregator_not_local_state(
        self, tab_harness, mock_state, mock_aggregator
    ):
        mock_state.models = {"local-model": _make_model(name="local-model")}
        mock_aggregator.get_all_models.return_value = {
            "gpu-rig": [{"name": "remote-model", "model_path": "/remote/models/remote-model.gguf"}]
        }
        healthy = ModelHealthResult(valid=True, exists=True, readable=True, size_bytes=2_000_000)

        with patch(
            "llauncher.core.model_health.check_model_health", return_value=healthy
        ) as mock_check:
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "gpu-rig"
            )

        assert not at.exception
        mock_aggregator.get_all_models.assert_called_once()
        mock_check.assert_called_once_with("/remote/models/remote-model.gguf")
        df = at.dataframe[0].value
        assert list(df["name"]) == ["remote-model"]
        assert list(df["node"]) == ["gpu-rig"]
        # Local target's state was never consulted for a remote render.
        mock_state.refresh.assert_not_called()

    def test_remote_target_accepts_model_objects_with_to_dict(
        self, tab_harness, mock_state, mock_aggregator
    ):
        """Aggregator rows may be model-like objects, not only plain dicts."""
        model_obj = MagicMock()
        model_obj.to_dict.return_value = {
            "name": "obj-model",
            "model_path": "/remote/models/obj-model.gguf",
        }
        mock_aggregator.get_all_models.return_value = {"gpu-rig": [model_obj]}
        healthy = ModelHealthResult(valid=True, exists=True, readable=True, size_bytes=2_000_000)

        with patch(
            "llauncher.core.model_health.check_model_health", return_value=healthy
        ):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "gpu-rig"
            )

        assert not at.exception
        model_obj.to_dict.assert_called_once()
        df = at.dataframe[0].value
        assert list(df["name"]) == ["obj-model"]


class TestModelRegistrySizeFormatting:
    """The rendered ``size`` column is a human-readable string per byte scale.

    ``_format_size`` is exercised indirectly through a real health result
    (rather than mocked) so the test pins the *rendered* size string a
    maintainer would actually see in the table.
    """

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
        mock_state.models = {"test-model": _make_model()}
        # Small sizes are naturally "not valid" per the 1 MiB heuristic, but
        # the size column renders from ``size_bytes`` regardless of validity.
        health = ModelHealthResult(
            valid=(size_bytes >= 1024 * 1024),
            exists=True,
            readable=True,
            size_bytes=size_bytes,
            reason=None if size_bytes >= 1024 * 1024 else "too small",
        )

        with patch("llauncher.core.model_health.check_model_health", return_value=health):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        df = at.dataframe[0].value
        assert list(df["size"]) == [expected]


class TestModelRegistryLocalTargetRefresh:
    """The local target's model list is sourced by refreshing local state."""

    def test_local_target_calls_state_refresh_before_reading_models(
        self, tab_harness, mock_state, mock_aggregator
    ):
        mock_state.models = {"test-model": _make_model()}
        healthy = ModelHealthResult(valid=True, exists=True, readable=True, size_bytes=2_000_000)

        with patch(
            "llauncher.core.model_health.check_model_health", return_value=healthy
        ):
            at = tab_harness(
                render_model_registry, mock_state, None, mock_aggregator, "local"
            )

        assert not at.exception
        mock_state.refresh.assert_called_once()
