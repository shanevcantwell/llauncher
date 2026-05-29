"""Streamlit UI for llauncher with multi-node support."""

import os

import streamlit as st

from llauncher.core import settings
from llauncher.state import LauncherState
from llauncher.remote.registry import NodeRegistry
from llauncher.remote.state import RemoteAggregator
from llauncher.ui.components.node_selector import render_node_selector


# Configure page
st.set_page_config(
    page_title="llauncher",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)


def get_state() -> LauncherState:
    """Get or create the launcher state (using session state for persistence)."""
    if "state" not in st.session_state:
        st.session_state["state"] = LauncherState()
    return st.session_state["state"]


def get_registry() -> NodeRegistry:
    """Get or create the node registry (using session state for persistence)."""
    if "registry" not in st.session_state:
        st.session_state["registry"] = NodeRegistry()
    return st.session_state["registry"]


def get_aggregator() -> RemoteAggregator:
    """Get or create the remote aggregator (using session state for persistence)."""
    if "aggregator" not in st.session_state:
        st.session_state["aggregator"] = RemoteAggregator(get_registry())
    return st.session_state["aggregator"]


def is_agent_ready(registry: NodeRegistry) -> bool:
    """Check if the local agent is ready.

    Args:
        registry: NodeRegistry instance.

    Returns:
        True if agent is responding, False otherwise.
    """
    return registry.is_local_agent_ready()


def show_agent_down_banner() -> None:
    """Render an "agent down" banner with start instructions.

    M4 Slice 12 (issue #49 / audit H2) replaces the old auto-spawn-plus-
    loading-screen flow with this passive banner. ADR-009 prescribes a
    symmetric hub-spoke topology — the local agent is a peer like any
    other, started by the user (typically via ``llauncher agent
    start``), not auto-spawned by whichever tool happened to start
    first. Auto-spawning made "local" a special case in a way the
    architecture explicitly disclaims.

    Caller is expected to render the page-level title (``st.title``)
    *before* this and to ``st.stop()`` *after* it, so the rest of the
    dashboard does not render against a missing agent.
    """
    agent_port = int(os.getenv("LLAUNCHER_AGENT_PORT", "8765"))
    st.error(
        "**Local agent is not running.**\n\n"
        "Start it in a separate terminal with the installed console "
        "script:\n\n"
        "```bash\n"
        "llauncher-agent\n"
        "```\n\n"
        "Or, if you came in via the runner scripts:\n\n"
        "```bash\n"
        "./run.sh agent     # Linux / macOS\n"
        "run.bat agent      :: Windows\n"
        "```\n\n"
        f"Then refresh this page. The agent listens on port "
        f"``{agent_port}`` (override via ``LLAUNCHER_AGENT_PORT``) and "
        f"reads/writes state under ``{settings.LAUNCHER_RUN_DIR.parent}``.",
        icon="🛑",
    )
    with st.expander("Why doesn't the UI start the agent for me?"):
        st.markdown(
            "Earlier versions of llauncher auto-spawned the local agent on UI "
            "load. ADR-009 ratified a symmetric hub-spoke topology where "
            "every node — including ``local`` — is a peer started "
            "deliberately by the user. Having the UI fork a daemon implicitly "
            "made ``local`` a special case and obscured failures (e.g. a port "
            "collision) behind a generic spinner. The CLI command above is "
            "the single, observable way to bring the agent up."
        )


def main():
    """Main entry point for the Streamlit app."""
    # Get state and registry
    state = get_state()
    registry = get_registry()
    aggregator = get_aggregator()

    # Page chrome lives here, in the caller, regardless of whether the
    # agent is up — so an accidental refactor that drops ``st.stop()``
    # cannot produce a double-title rendering.
    st.title("🚀 llauncher")

    # Check if agent is ready. M4 Slice 12 (issue #49) removed the
    # subprocess auto-spawn path; the UI now passively reports "down"
    # and instructs the user to run ``llauncher-agent`` themselves.
    if not is_agent_ready(registry):
        show_agent_down_banner()
        st.stop()
    st.markdown("Manage your llama.cpp servers across multiple nodes")

    # Sidebar
    with st.sidebar:
        st.header("Controls")

        # Refresh button
        if st.button("🔄 Refresh All", use_container_width=True):
            state.refresh()
            registry.refresh_all()
            st.toast("Refreshed all nodes", icon="🔄")
            st.rerun()

        st.divider()

        # Node selector — reusable component (issue #48 / m4-design Slice 11).
        # Writes to st.session_state[TARGET_NODE_KEY]. "local" is the default.
        st.subheader("🖥️ Node")
        selected = render_node_selector(registry)

    # Tab navigation. Stage 2 of #50 collapses Manager + Model Registry +
    # Dashboard's verb surface into the Models tab; Dashboard becomes a
    # glance-only view.
    tab1, tab2, tab3, tab4 = st.tabs(
        ["📊 Dashboard", "🗂️ Models", "🖥️ Nodes", "📝 Audit"]
    )

    with tab1:
        from llauncher.ui.tabs.dashboard import render_dashboard

        render_dashboard(state, registry, aggregator, selected)

    with tab2:
        from llauncher.ui.tabs.models import render_models_tab

        render_models_tab(state, registry, aggregator, selected)

    with tab3:
        from llauncher.ui.tabs.nodes import render_nodes_tab

        render_nodes_tab(registry, aggregator)

    with tab4:
        from llauncher.ui.tabs.audit import render_audit_tab

        render_audit_tab(selected, registry)


if __name__ == "__main__":
    main()
