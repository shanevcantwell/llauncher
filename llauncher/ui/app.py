"""Streamlit UI for llauncher with multi-node support."""

import subprocess
import time

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


@st.cache_resource
def get_state() -> LauncherState:
    """Get or create the launcher state (cached across reruns)."""
    return LauncherState()


@st.cache_resource
def get_registry() -> NodeRegistry:
    """Get or create the node registry (cached across reruns)."""
    return NodeRegistry()


@st.cache_resource
def get_aggregator() -> RemoteAggregator:
    """Get or create the remote aggregator (cached across reruns)."""
    registry = get_registry()
    return RemoteAggregator(registry)


def is_agent_ready(registry: NodeRegistry) -> bool:
    """Check if the local agent is ready.

    Args:
        registry: NodeRegistry instance.

    Returns:
        True if agent is responding, False otherwise.
    """
    import os
    import socket

    AGENT_PORT = int(os.getenv("LAUNCHER_AGENT_PORT", "8765"))

    # Check if local node exists and is online
    local_node = registry.get_node("local")
    if local_node and local_node.ping():
        return True

    # Check if port is in use
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", AGENT_PORT))
            # Something is running - add to registry if not present
            if not local_node:
                registry.add_node("local", "localhost", AGENT_PORT, overwrite=True)
            return True
        except (ConnectionRefusedError, TimeoutError, OSError):
            pass

    return False


def start_agent_background(registry: NodeRegistry) -> None:
    """Start the agent as a detached background process.

    Args:
        registry: NodeRegistry instance.
    """
    import os
    import sys

    AGENT_PORT = int(os.getenv("LAUNCHER_AGENT_PORT", "8765"))

    # Cross-platform process detachment:
    # - Windows: CREATE_NEW_PROCESS_GROUP detaches from console
    # - Unix: start_new_session creates new session (daemon-like)
    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen(["llauncher-agent"], **kwargs)

    # Add to registry if not present
    if not registry.get_node("local"):
        registry.add_node("local", "localhost", AGENT_PORT, overwrite=True)


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
    tab1, tab2 = st.tabs(["📊 Dashboard", "🖥️ Nodes"])

    with tab1:
        from llauncher.ui.tabs.dashboard import render_dashboard

        render_dashboard(state, registry, aggregator, st.session_state.get("selected_node"))

    with tab2:
        from llauncher.ui.tabs.nodes import render_nodes_tab

        render_nodes_tab(registry, aggregator)


if __name__ == "__main__":
    main()
