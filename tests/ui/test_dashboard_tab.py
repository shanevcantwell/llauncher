"""Streamlit ``AppTest`` tests for the Dashboard tab (``llauncher/ui/tabs/dashboard.py``).

Per the module's own docstring (M4 Slice 13 stage 2 / #50), the Dashboard is
a **view-only glance surface**: it lists the running servers on the selected
target plus the configured-but-stopped models, and dispatches *no*
orchestration verbs at all (start/stop/swap/edit/add/delete all migrated to
``models.py``). The contract this file pins is therefore purely
render/data-shaping, not dispatch parity — there is no ``ops.<verb>`` call
for this module to special-case:

1. **Empty state** — no running servers and no configured models renders the
   "no servers" info banner and the "none configured" caption, through the
   mocked ``state`` / ``aggregator`` facades only (no direct HTTP, no raw
   process inspection).
2. **Running-servers table** — a running local server surfaces via
   ``state.running`` in the "Running" dataframe with Model/Port/PID/Uptime
   columns.
3. **Stopped-models table** — a configured-but-not-running model surfaces in
   the "Configured (not running)" dataframe, sorted case-insensitively by
   name, and a running model is excluded from it.
4. **Remote-target branch** — passing a non-``LOCAL_NODE`` target routes
   server/model lookups through ``RemoteAggregator`` (``get_all_servers`` /
   ``get_all_models``) instead of local ``state``, filtered to that node.

The two pure helpers (``get_servers_to_display`` / ``get_models_to_display``)
additionally get direct plain-pytest tests below (no Streamlit context
needed) since they contain the only branching logic in the module.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from llauncher.models.config import ModelConfig, RunningServer
from llauncher.remote.node import RemoteServerInfo
from llauncher.ui.components.node_selector import LOCAL_NODE
from llauncher.ui.tabs.dashboard import (
    get_models_to_display,
    get_servers_to_display,
    render_dashboard,
)


def _model_config(name: str, model_path: str = "/path/to/model.gguf") -> ModelConfig:
    return ModelConfig.from_dict_unvalidated({"name": name, "model_path": model_path})


def _running_server(
    config_name: str = "model-a", pid: int = 1234, port: int = 8080
) -> RunningServer:
    return RunningServer(
        pid=pid,
        port=port,
        config_name=config_name,
        start_time=datetime.now(),
        logs_path="/logs/model-a.log",
    )


def _remote_server_info(
    node_name: str = "gpu-rig", config_name: str = "remote-model", port: int = 9090
) -> RemoteServerInfo:
    return RemoteServerInfo(
        node_name=node_name,
        pid=4321,
        port=port,
        config_name=config_name,
        start_time=datetime.now().isoformat(),
        uptime_seconds=120,
    )


# ---------------------------------------------------------------------------
# Pure-helper tests — no Streamlit context needed.
# ---------------------------------------------------------------------------
class TestGetServersToDisplay:
    """``get_servers_to_display`` sources local vs. remote servers correctly."""

    def test_local_target_with_no_running_servers_returns_empty_list(
        self, mock_state, mock_registry, mock_aggregator
    ):
        mock_state.running = {}

        servers = get_servers_to_display(
            mock_state, mock_registry, mock_aggregator, LOCAL_NODE
        )

        assert servers == []
        mock_state.refresh.assert_called_once()

    def test_local_target_maps_running_server_to_remote_server_info(
        self, mock_state, mock_registry, mock_aggregator
    ):
        srv = _running_server(config_name="model-a", pid=42, port=8080)
        mock_state.running = {8080: srv}

        servers = get_servers_to_display(
            mock_state, mock_registry, mock_aggregator, LOCAL_NODE
        )

        assert len(servers) == 1
        info = servers[0]
        assert info.node_name == LOCAL_NODE
        assert info.config_name == "model-a"
        assert info.pid == 42
        assert info.port == 8080

    def test_remote_target_filters_aggregator_servers_by_node_name(
        self, mock_state, mock_registry, mock_aggregator
    ):
        on_target = _remote_server_info(node_name="gpu-rig", config_name="on-target")
        other_node = _remote_server_info(node_name="other-rig", config_name="off-target")
        mock_aggregator.get_all_servers.return_value = [on_target, other_node]

        servers = get_servers_to_display(
            mock_state, mock_registry, mock_aggregator, "gpu-rig"
        )

        assert servers == [on_target]
        # Remote path never touches local state.
        mock_state.refresh.assert_not_called()


class TestGetModelsToDisplay:
    """``get_models_to_display`` sources local vs. remote model configs."""

    def test_local_target_returns_configured_models_as_dicts(
        self, mock_state, mock_registry, mock_aggregator
    ):
        mock_state.models = {"model-a": _model_config("model-a")}

        models = get_models_to_display(
            mock_state, mock_registry, mock_aggregator, LOCAL_NODE
        )

        assert len(models) == 1
        assert models[0]["name"] == "model-a"

    def test_remote_target_with_unknown_node_returns_empty_list(
        self, mock_state, mock_registry, mock_aggregator
    ):
        mock_aggregator.get_all_models.return_value = {}

        models = get_models_to_display(
            mock_state, mock_registry, mock_aggregator, "unknown-node"
        )

        assert models == []

    def test_remote_target_converts_model_configs_via_to_dict(
        self, mock_state, mock_registry, mock_aggregator
    ):
        mock_aggregator.get_all_models.return_value = {
            "gpu-rig": [_model_config("remote-model")]
        }

        models = get_models_to_display(
            mock_state, mock_registry, mock_aggregator, "gpu-rig"
        )

        assert len(models) == 1
        assert models[0]["name"] == "remote-model"

    def test_remote_target_passes_through_plain_dicts_unchanged(
        self, mock_state, mock_registry, mock_aggregator
    ):
        """Defensive branch: a raw dict (no ``to_dict``) passes through as-is."""
        raw = {"name": "already-a-dict"}
        mock_aggregator.get_all_models.return_value = {"gpu-rig": [raw]}

        models = get_models_to_display(
            mock_state, mock_registry, mock_aggregator, "gpu-rig"
        )

        assert models == [raw]


# ---------------------------------------------------------------------------
# Rendered-output (AppTest) behavioral tests.
# ---------------------------------------------------------------------------
class TestDashboardEmptyState:
    """No running servers and no configured models."""

    def test_empty_dashboard_shows_no_servers_banner_and_none_configured_caption(
        self, tab_harness, mock_state, mock_registry, mock_aggregator
    ):
        mock_state.running = {}
        mock_state.models = {}

        at = tab_harness(
            render_dashboard, mock_state, mock_registry, mock_aggregator, LOCAL_NODE
        )

        assert not at.exception
        assert at.header[0].value == "📊 Dashboard"
        banner = next(el.value for el in at.info if "No servers running" in el.value)
        assert LOCAL_NODE in banner
        captions = {el.value for el in at.caption}
        assert any("All configured models are running" in c for c in captions)
        # No dataframe rendered at all in the fully-empty case.
        assert len(at.dataframe) == 0


class TestDashboardRunningServersTable:
    """A running local server surfaces in the "Running" table."""

    def test_running_local_server_renders_in_running_dataframe(
        self, tab_harness, mock_state, mock_registry, mock_aggregator
    ):
        mock_state.running = {8080: _running_server(config_name="model-a", pid=42, port=8080)}
        mock_state.models = {"model-a": _model_config("model-a")}

        at = tab_harness(
            render_dashboard, mock_state, mock_registry, mock_aggregator, LOCAL_NODE
        )

        assert not at.exception
        assert len(at.dataframe) == 1
        df = at.dataframe[0].value
        assert list(df["Model"]) == ["model-a"]
        assert list(df["Port"]) == [8080]
        assert list(df["PID"]) == [42]
        # A running model is excluded from "Configured (not running)".
        captions = {el.value for el in at.caption}
        assert any("All configured models are running" in c for c in captions)

    def test_no_running_servers_renders_info_banner_not_dataframe(
        self, tab_harness, mock_state, mock_registry, mock_aggregator
    ):
        mock_state.running = {}
        mock_state.models = {"model-a": _model_config("model-a")}

        at = tab_harness(
            render_dashboard, mock_state, mock_registry, mock_aggregator, LOCAL_NODE
        )

        assert not at.exception
        assert any("No servers running" in el.value for el in at.info)


class TestDashboardStoppedModelsTable:
    """Configured-but-stopped models surface in the second table, sorted."""

    def test_stopped_models_render_sorted_case_insensitively(
        self, tab_harness, mock_state, mock_registry, mock_aggregator
    ):
        mock_state.running = {}
        mock_state.models = {
            "Zeta": _model_config("Zeta", "/path/z.gguf"),
            "alpha": _model_config("alpha", "/path/a.gguf"),
        }

        at = tab_harness(
            render_dashboard, mock_state, mock_registry, mock_aggregator, LOCAL_NODE
        )

        assert not at.exception
        assert len(at.dataframe) == 1
        df = at.dataframe[0].value
        assert list(df["Model"]) == ["alpha", "Zeta"]

    def test_running_model_excluded_from_stopped_table(
        self, tab_harness, mock_state, mock_registry, mock_aggregator
    ):
        mock_state.running = {8080: _running_server(config_name="model-a", pid=1, port=8080)}
        mock_state.models = {
            "model-a": _model_config("model-a"),
            "model-b": _model_config("model-b", "/path/b.gguf"),
        }

        at = tab_harness(
            render_dashboard, mock_state, mock_registry, mock_aggregator, LOCAL_NODE
        )

        assert not at.exception
        # Two dataframes: Running (model-a) and Configured/not-running (model-b only).
        assert len(at.dataframe) == 2
        stopped_df = at.dataframe[1].value
        assert list(stopped_df["Model"]) == ["model-b"]


class TestDashboardRemoteTarget:
    """Selecting a remote node routes both tables through the aggregator."""

    def test_remote_target_renders_servers_and_models_from_aggregator(
        self, tab_harness, mock_state, mock_registry, mock_aggregator, forbid_direct_http
    ):
        mock_aggregator.get_all_servers.return_value = [
            _remote_server_info(node_name="gpu-rig", config_name="remote-model", port=9090)
        ]
        mock_aggregator.get_all_models.return_value = {
            "gpu-rig": [_model_config("remote-model")]
        }

        with forbid_direct_http():
            at = tab_harness(
                render_dashboard, mock_state, mock_registry, mock_aggregator, "gpu-rig"
            )

        assert not at.exception
        assert "gpu-rig" in at.caption[0].value
        assert len(at.dataframe) == 1
        df = at.dataframe[0].value
        assert list(df["Model"]) == ["remote-model"]
        assert list(df["Port"]) == [9090]
        # Local state must never be touched on the remote-target path.
        mock_state.refresh.assert_not_called()

    def test_remote_target_with_no_data_shows_empty_state(
        self, tab_harness, mock_state, mock_registry, mock_aggregator
    ):
        mock_aggregator.get_all_servers.return_value = []
        mock_aggregator.get_all_models.return_value = {}

        at = tab_harness(
            render_dashboard, mock_state, mock_registry, mock_aggregator, "gpu-rig"
        )

        assert not at.exception
        assert any("No servers running" in el.value for el in at.info)
        assert any(
            "All configured models are running" in el.value for el in at.caption
        )
