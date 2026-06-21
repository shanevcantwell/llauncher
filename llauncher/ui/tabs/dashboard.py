"""Dashboard tab — glance-only view of running servers (M4 Slice 13, stage 2).

Stage 2 of #50 reduced this tab to a *view-only* surface: it lists the
running servers on the selected target plus the configured-but-stopped
models, and that's it. All verbs (start/stop/swap/edit/add/delete) and
their forms migrated to :mod:`models`. The "All Nodes" cross-node
listing is gone too — ``target`` is always a real node name now, and
this module no longer accepts ``None``.

Why split? The legacy Dashboard tried to be *both* a status board and a
management console. That dual role is what kept seducing us into adding
more verbs to it (ADR-010's port-on-config used to live here). Splitting
the view from the verbs locks the dashboard's surface area down.
"""

from __future__ import annotations

import streamlit as st

from llauncher.remote.node import RemoteServerInfo
from llauncher.remote.registry import NodeRegistry
from llauncher.remote.state import RemoteAggregator
from llauncher.state import LauncherState
from llauncher.ui.components.node_selector import LOCAL_NODE
from llauncher.ui.utils import format_uptime


def get_servers_to_display(
    state: LauncherState,
    registry: NodeRegistry,
    aggregator: RemoteAggregator,
    target: str,
) -> list[RemoteServerInfo]:
    """Return the running servers on ``target`` as :class:`RemoteServerInfo`.

    Args:
        state: The local launcher state.
        registry: Node registry. Currently unused — kept in the
            signature for symmetry with the rest of the tab API and to
            leave room for future "is the agent reachable?" warnings.
        aggregator: Remote aggregator.
        target: Selected target node. Pass :data:`LOCAL_NODE` for the
            local agent; any other string for a remote peer.

    Returns:
        The list of server records the dashboard should render. Empty
        list when ``target`` has no running servers.
    """
    del registry  # reserved for future use; see docstring.

    servers: list[RemoteServerInfo] = []

    if target == LOCAL_NODE:
        state.refresh()
        for _, server in state.running.items():
            servers.append(
                RemoteServerInfo(
                    node_name=LOCAL_NODE,
                    pid=server.pid,
                    port=server.port,
                    config_name=server.config_name,
                    start_time=server.start_time.isoformat(),
                    uptime_seconds=server.uptime_seconds(),
                    logs_path=server.logs_path,
                )
            )
        return servers

    # Remote target.
    for server in aggregator.get_all_servers():
        if server.node_name == target:
            servers.append(server)
    return servers


def get_models_to_display(
    state: LauncherState,
    registry: NodeRegistry,
    aggregator: RemoteAggregator,
    target: str,
) -> list[dict]:
    """Return the configured models for ``target`` as plain dicts.

    Args:
        state: The local launcher state.
        registry: Node registry. Unused — see :func:`get_servers_to_display`.
        aggregator: Remote aggregator.
        target: Selected target node.

    Returns:
        List of model dicts (as ``ModelConfig.to_dict()`` would yield),
        in registry order.
    """
    del registry

    if target == LOCAL_NODE:
        return [m.to_dict() for m in state.models.values()]

    all_remote = aggregator.get_all_models()
    raw_models = all_remote.get(target, [])
    return [m.to_dict() if hasattr(m, "to_dict") else m for m in raw_models]


def render_dashboard(
    state: LauncherState,
    registry: NodeRegistry,
    aggregator: RemoteAggregator,
    target: str,
) -> None:
    """Render the read-only dashboard for ``target``.

    Args:
        state: The local launcher state.
        registry: Node registry.
        aggregator: Remote aggregator.
        target: Selected target node from the sidebar.
    """
    st.header("📊 Dashboard")
    st.caption(
        f"Glance view for **{target}**. Use the **Models** tab to "
        f"start, stop, edit, or add models."
    )

    servers = get_servers_to_display(state, registry, aggregator, target)
    models = get_models_to_display(state, registry, aggregator, target)

    # ── Running servers ──────────────────────────────────────────
    st.subheader("Running")
    if not servers:
        st.info(f"No servers running on **{target}**.")
    else:
        rows = [
            {
                "Model": s.config_name,
                "Port": s.port,
                "PID": s.pid,
                "Uptime": format_uptime(s.uptime_seconds),
            }
            for s in servers
        ]
        # Lazy pandas import keeps the module bootable on minimal envs.
        # (Streamlit ships pandas, so this is belt-and-suspenders.)
        import pandas as pd

        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')

    # ── Configured-but-stopped models ───────────────────────────
    running_names = {s.config_name for s in servers}
    stopped = [m for m in models if m["name"] not in running_names]

    st.subheader("Configured (not running)")
    if not stopped:
        st.caption("All configured models are running, or none are configured.")
        return

    # Sort alphabetically (case-insensitive) for a stable display order.
    stopped_sorted = sorted(stopped, key=lambda m: m["name"].lower())
    rows = [
        {
            "Model": m["name"],
            "Path": m.get("model_path", ""),
            "GPU layers": m.get("n_gpu_layers", "—"),
        }
        for m in stopped_sorted
    ]
    import pandas as pd

    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
