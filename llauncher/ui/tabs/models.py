"""Models tab — registry table + add/edit forms + per-model cards (M4 Slice 13).

Stage 2 of #50 collapses the old "Manager", "Model Registry" and
"Dashboard" verbs into two tabs: this one owns *everything* model-shaped
(registry view, CRUD forms, per-model start/stop/swap cards), and
:mod:`dashboard` shrinks to a glance-only view of running servers.

Why one tab? Stage 1's tab inventory had four model-touching surfaces
(Manager, Registry, Dashboard's add/edit forms, Dashboard's per-model
cards) split across three tabs, all sharing the same underlying
``state.models``. The split was historical; the user-facing mental model
is "things I can do to a model" — registry view, configure, run. Folding
them into one tab matches that mental model and lets the Dashboard be
the at-a-glance view it always wanted to be.

Per the Slice 13 spec, this module is a *composition root*: it imports
``forms.py`` and ``model_card.py`` rather than inlining their bodies, so
no single file pushes past the 800-line cap.
"""

from __future__ import annotations

import streamlit as st

from llauncher.remote.node import RemoteServerInfo
from llauncher.remote.registry import NodeRegistry
from llauncher.remote.state import RemoteAggregator
from llauncher.state import LauncherState
from llauncher.ui.components.node_selector import LOCAL_NODE
from llauncher.ui.tabs.forms import render_add_model, render_edit_model
from llauncher.ui.tabs.model_card import render_model_card
from llauncher.ui.tabs.model_registry import render_model_registry


def render_models_tab(
    state: LauncherState,
    registry: NodeRegistry,
    aggregator: RemoteAggregator,
    target: str,
) -> None:
    """Render the consolidated Models tab.

    Layout, top to bottom:

    1. Registry health table (delegated to
       :func:`model_registry.render_model_registry`, scoped to ``target``).
    2. "Add New Model" expander (local-only — config CRUD lives on the
       local node by design).
    3. Per-model cards via :func:`model_card.render_model_card`. When
       any ``editing_<name>`` flag is set in ``st.session_state`` the
       edit form replaces the card grid (matching the legacy
       Dashboard UX so the user's hand muscle memory keeps working).

    Args:
        state: The local launcher state.
        registry: Node registry.
        aggregator: Remote aggregator.
        target: Selected target node from the sidebar selector. Always
            a string after #50 stage 1 dropped the "All Nodes" branch.
    """
    st.header("🗂️ Models")

    # ── Registry table ───────────────────────────────────────────
    # Delegate to the existing dataframe renderer. Scoped to ``target``
    # so the user sees only the node they selected; the legacy
    # ``selected_node=None`` "all nodes" branch has been retired.
    render_model_registry(state, registry, aggregator, target)

    st.divider()

    # ── Edit-mode short-circuit ──────────────────────────────────
    # Mirror the dashboard behaviour: if the user clicked "✏️ Edit" on
    # any model card, replace the rest of the page with the edit form
    # so the user can't simultaneously be editing one model and
    # starting another.
    editing_model = _get_editing_model(state)
    if editing_model:
        render_edit_model(state, editing_model)
        return

    # ── Add New Model ────────────────────────────────────────────
    with st.expander("➕ Add New Model", expanded=False):
        render_add_model(state)

    # ── Per-model cards ──────────────────────────────────────────
    if not state.models and target == LOCAL_NODE:
        st.info(
            "No models configured. Use the 'Add New Model' section above to add one."
        )
        return

    st.subheader("Model cards")

    # Build the same running-server lookup the legacy dashboard used so
    # ``render_model_card`` gets a fully-resolved ``running_server`` arg
    # rather than rediscovering it.
    running_map = _build_running_map(state, aggregator, target)

    if target == LOCAL_NODE:
        models_for_target = [m.to_dict() for m in state.models.values()]
    else:
        all_remote_models = aggregator.get_all_models()
        raw_models = all_remote_models.get(target, [])
        models_for_target = [
            m.to_dict() if hasattr(m, "to_dict") else m for m in raw_models
        ]

    sorted_models = sorted(models_for_target, key=lambda m: m["name"].lower())
    for model in sorted_models:
        running_servers = running_map.get((target, model["name"]), [])
        if not running_servers:
            render_model_card(state, registry, aggregator, target, model, None)
            continue
        for running_server in running_servers:
            widget_key_suffix = (
                f"_{running_server.port}" if len(running_servers) > 1 else ""
            )
            render_model_card(
                state,
                registry,
                aggregator,
                target,
                model,
                running_server,
                widget_key_suffix=widget_key_suffix,
            )


def _get_editing_model(state: LauncherState) -> str | None:
    """Find the model currently being edited.

    Returns:
        The model name with an ``editing_<name>`` flag set, or ``None``
        if no edit is in progress.
    """
    for name in state.models:
        if st.session_state.get(f"editing_{name}"):
            return name
    return None


def _build_running_map(
    state: LauncherState,
    aggregator: RemoteAggregator,
    target: str,
) -> dict[tuple[str, str], list[RemoteServerInfo]]:
    """Group running servers by ``(node_name, config_name)`` for ``target``."""
    running_map: dict[tuple[str, str], list[RemoteServerInfo]] = {}

    if target == LOCAL_NODE:
        state.refresh()
        for _, server in state.running.items():
            info = RemoteServerInfo(
                node_name=LOCAL_NODE,
                pid=server.pid,
                port=server.port,
                config_name=server.config_name,
                start_time=server.start_time.isoformat(),
                uptime_seconds=server.uptime_seconds(),
                logs_path=server.logs_path,
            )
            running_map.setdefault((LOCAL_NODE, server.config_name), []).append(info)
    else:
        for server in aggregator.get_all_servers():
            if server.node_name == target:
                running_map.setdefault(
                    (server.node_name, server.config_name), []
                ).append(server)

    return running_map
