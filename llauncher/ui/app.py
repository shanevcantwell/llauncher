"""Streamlit UI for llauncher with multi-node support."""

import streamlit as st

from llauncher.state import LauncherState
from llauncher.remote.registry import NodeRegistry
from llauncher.remote.state import RemoteAggregator
from llauncher.remote.node import NodeStatus


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


def start_agent_background(registry: NodeRegistry) -> None:
    """Start the agent as a detached background process.

    Args:
        registry: NodeRegistry instance.
    """
    # Note: We don't check the return value here as the UI handles errors via session state
    registry.start_local_agent()


def show_loading_screen() -> None:
    """Show a full-screen loading overlay.

    This function only renders the loading screen and returns immediately.
    The caller should use st.stop() or st.rerun() to control flow.
    """
    st.markdown("""
    <style>
    .loading-screen {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.85);
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        z-index: 9999;
        color: white;
        font-family: sans-serif;
    }
    .loading-spinner {
        border: 4px solid rgba(255, 255, 255, 0.1);
        border-top: 4px solid #4CAF50;
        border-radius: 50%;
        width: 60px;
        height: 60px;
        animation: spin 1s linear infinite;
        margin-bottom: 20px;
    }
    @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="loading-screen">
        <div class="loading-spinner"></div>
        <h2>🚀 Starting llauncher...</h2>
        <p>Initializing agent and loading models</p>
    </div>
    """, unsafe_allow_html=True)


def main():
    """Main entry point for the Streamlit app."""
    # Get state and registry
    state = get_state()
    registry = get_registry()
    aggregator = get_aggregator()

    # Track startup state in session
    if "agent_startup_started" not in st.session_state:
        st.session_state["agent_startup_started"] = False

    # Check if agent is ready
    agent_ready = is_agent_ready(registry)

    if not agent_ready:
        # Show loading screen
        show_loading_screen()

        # Start agent if not already started
        if not st.session_state["agent_startup_started"]:
            try:
                start_agent_background(registry)
                st.session_state["agent_startup_started"] = True
            except Exception as e:
                st.session_state["agent_startup_error"] = str(e)

        # Check if we have an error
        if st.session_state.get("agent_startup_error"):
            st.stop()

        # Rerun to check if agent is ready now
        st.rerun()
        st.stop()  # Always stop after loading screen

    # Agent is ready - clear startup state and show main UI
    st.session_state["agent_startup_started"] = False
    st.session_state.pop("agent_startup_error", None)

    st.title("🚀 llauncher")
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

        # Node selector
        st.subheader("🖥️ Node")

        # Build node options with offline filtering
        show_offline = st.session_state.get("show_offline_nodes", True)
        node_options = ["All Nodes"]
        for node in registry:
            is_online = node.status == NodeStatus.ONLINE
            if not is_online and not show_offline:
                continue  # Skip offline nodes if toggle is off
            status = "🟢" if is_online else "⚫"
            node_options.append(f"{status} {node.name}")

        selected = st.selectbox(
            "Select Node",
            options=node_options,
            index=0,
            help="Select a specific node or view all nodes",
        )

        # Store selected node in session state
        if selected == "All Nodes":
            st.session_state["selected_node"] = None
        else:
            st.session_state["selected_node"] = selected.replace("🟢 ", "").replace("⚫ ", "")

        st.divider()

        # Options
        st.subheader("🔧 Options")
        st.session_state["show_offline_nodes"] = st.checkbox(
            "Show offline nodes",
            value=st.session_state.get("show_offline_nodes", True),
            help="Uncheck to hide offline nodes from the selector",
        )

    # Tab navigation
    tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🖥️ Nodes", "🗂️ Model Registry"])

    with tab1:
        from llauncher.ui.tabs.dashboard import render_dashboard

        render_dashboard(state, registry, aggregator, st.session_state.get("selected_node"))

    with tab2:
        from llauncher.ui.tabs.nodes import render_nodes_tab

        render_nodes_tab(registry, aggregator)

    with tab3:
        from llauncher.ui.tabs.model_registry import render_model_registry

        render_model_registry(state, registry, aggregator, st.session_state.get("selected_node"))


if __name__ == "__main__":
    main()
