"""Dashboard tab showing model cards with status (multi-node support)."""

import streamlit as st

from llauncher.state import LauncherState
from llauncher.remote.registry import NodeRegistry
from llauncher.remote.state import RemoteAggregator
from llauncher.remote.node import RemoteServerInfo

from llauncher.ui.tabs.model_card import render_model_card, _handle_stop, _handle_start
from llauncher.ui.tabs.forms import render_add_model, render_edit_model


def get_servers_to_display(
    state: LauncherState,
    registry: NodeRegistry | None = None,
    aggregator: RemoteAggregator | None = None,
    selected_node: str | None = None,
) -> list:
    """Get servers to display based on current view.

    Args:
        state: The launcher state (local).
        registry: NodeRegistry for remote nodes.
        aggregator: RemoteAggregator for multi-node state.
        selected_node: Name of selected node or None for all.

    Returns:
        List of RemoteServerInfo to display.
    """
    servers = []

    if registry and aggregator and selected_node:
        # Show only selected node
        if selected_node == "local":
            # Local node only
            state.refresh()
            for port, server in state.running.items():
                servers.append(
                    RemoteServerInfo(
                        node_name="local",
                        pid=server.pid,
                        port=server.port,
                        config_name=server.config_name,
                        start_time=server.start_time.isoformat(),
                        uptime_seconds=server.uptime_seconds(),
                        logs_path=server.logs_path,
                    )
                )
        else:
            # Specific remote node
            servers.extend(get_node_servers(aggregator, selected_node))
    elif registry and aggregator:
        # Show all nodes (remote + local)
        servers.extend(aggregator.get_all_servers())
        state.refresh()
        for port, server in state.running.items():
            servers.append(
                RemoteServerInfo(
                    node_name="local",
                    pid=server.pid,
                    port=server.port,
                    config_name=server.config_name,
                    start_time=server.start_time.isoformat(),
                    uptime_seconds=server.uptime_seconds(),
                    logs_path=server.logs_path,
                )
            )
    else:
        # Show only local
        state.refresh()
        for port, server in state.running.items():
            servers.append(
                RemoteServerInfo(
                    node_name="local",
                    pid=server.pid,
                    port=server.port,
                    config_name=server.config_name,
                    start_time=server.start_time.isoformat(),
                    uptime_seconds=server.uptime_seconds(),
                    logs_path=server.logs_path,
                )
            )

    return servers


def get_models_to_display(
    state: LauncherState,
    registry: NodeRegistry | None = None,
    aggregator: RemoteAggregator | None = None,
    selected_node: str | None = None,
) -> dict[str, list[dict]]:
    """Get models to display based on current view.

    Args:
        state: The launcher state (local).
        registry: NodeRegistry for remote nodes.
        aggregator: RemoteAggregator for multi-node state.
        selected_node: Name of selected node or None for all.

    Returns:
        Dictionary mapping node names to their model lists.
    """
    all_models = {}

    if registry and aggregator and selected_node:
        if selected_node == "local":
            # Show only local models when "local" node is selected
            all_models["local"] = [m.to_dict() for m in state.models.values()]
        else:
            # Show only selected remote node's models
            all_models = aggregator.get_all_models()
    elif registry and aggregator:
        # Show all models grouped by node (All Nodes view)
        all_models = aggregator.get_all_models()
        # Merge in local models for "All Nodes" view
        all_models["local"] = [m.to_dict() for m in state.models.values()]
    else:
        # Show only local models
        all_models["local"] = [m.to_dict() for m in state.models.values()]

    return all_models


def get_node_servers(aggregator: RemoteAggregator, node_name: str) -> list:
    """Get servers for a specific node."""
    all_servers = aggregator.get_all_servers()
    return [s for s in all_servers if s.node_name == node_name]


def render_dashboard(
    state: LauncherState,
    registry: NodeRegistry | None = None,
    aggregator: RemoteAggregator | None = None,
    selected_node: str | None = None,
) -> None:
    """Render the dashboard view.

    Args:
        state: The launcher state (local).
        registry: NodeRegistry for remote nodes.
        aggregator: RemoteAggregator for multi-node state.
        selected_node: Name of selected node or None for all.
    """
    st.header("📊 Dashboard")

    # Show node filter indicator
    if registry and len(registry) > 1:
        if selected_node:
            st.markdown(f"*Viewing: **{selected_node}** only*")
        else:
            st.markdown("*Viewing: All nodes*")

    # Check if we're editing a model
    editing_model = _get_editing_model(state)

    # Show edit form if editing
    if editing_model:
        render_edit_model(state, editing_model)
        return

    # Add New Model section (collapsible)
    with st.expander("➕ Add New Model", expanded=False):
        render_add_model(state)

    # Get servers and models to display using helper functions
    servers = get_servers_to_display(state, registry, aggregator, selected_node)
    all_models = get_models_to_display(state, registry, aggregator, selected_node)

    if not servers and not state.models:
        st.info("No models configured. Use the 'Add New Model' section above to add one.")
        return

    st.divider()
    st.subheader("Models")

    # Build a map of running servers for quick lookup (node_name, config_name) -> server info
    running_server_map: dict[tuple[str, str], RemoteServerInfo] = {}
    for server in servers:
        key = (server.node_name, server.config_name)
        running_server_map[key] = server

    if not all_models and not state.models:
        return

    # Render models by node
    for node_name, node_models in all_models.items():
        if selected_node and node_name != selected_node:
            continue

        st.markdown(f"**Node: {node_name}**")
        # Sort models alphabetically by name (case-insensitive)
        sorted_models = sorted(node_models, key=lambda m: m["name"].lower())
        for model in sorted_models:
            # Check if this model is currently running
            running_server = running_server_map.get((node_name, model["name"]))
            render_model_card(state, registry, aggregator, node_name, model, running_server)


def _get_editing_model(state: LauncherState) -> str | None:
    """Find the model currently being edited.

    Args:
        state: The launcher state.

    Returns:
        Model name being edited, or None if not editing.
    """
    for name in state.models:
        if st.session_state.get(f"editing_{name}"):
            return name
    return None
