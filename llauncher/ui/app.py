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


def ensure_local_agent(registry: NodeRegistry, show_loading: bool = False) -> bool:
    """Ensure local agent is running and registered.

    Args:
        registry: NodeRegistry instance.
        show_loading: If True, show loading progress in Streamlit.

    Returns:
        True if agent is ready, False otherwise.
    """
    import os
    import socket
    import sys

    # Use same default port as the agent config
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

    # Port is free, start the agent as a detached background process
    try:
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

        # Wait for agent to be ready with loading feedback
        max_wait = 10  # seconds
        waited = 0
        if show_loading:
            loading_container = st.container()
            with loading_container:
                progress_bar = st.progress(0)
                status_text = st.empty()
                status_text.text("⏳ Starting local agent...")

        while waited < max_wait:
            local_node = registry.get_node("local")
            if local_node and local_node.ping():
                if show_loading:
                    progress_bar.progress(100)
                    status_text.text("✅ Agent ready!")
                    time.sleep(0.5)
                    progress_bar.empty()
                    status_text.empty()
                return True

            time.sleep(0.5)
            waited += 0.5
            if show_loading:
                progress = min(int((waited / max_wait) * 100), 95)
                progress_bar.progress(progress)

        # Timeout - show error
        if show_loading:
            progress_bar.empty()
            status_text.empty()
            st.error("⚠️ Agent failed to start. Check console for errors.")

        return False
    except Exception as e:
        if show_loading:
            st.error(f"⚠️ Failed to start agent: {e}")
        return False


def show_loading_screen(registry: NodeRegistry) -> bool:
    """Show a full-screen loading overlay while waiting for the agent.

    Args:
        registry: NodeRegistry instance.

    Returns:
        True if agent is ready, False if failed.
    """
    # Full-screen loading overlay
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

    return ensure_local_agent(registry, show_loading=True)


def main():
    """Main entry point for the Streamlit app."""
    # Get state and registry
    state = get_state()
    registry = get_registry()
    aggregator = get_aggregator()

    # Check if we need to show loading screen
    local_node = registry.get_node("local")
    agent_ready = False

    if local_node and local_node.ping():
        # Agent already ready, skip loading screen
        agent_ready = True
    else:
        # Show loading screen and wait for agent
        agent_ready = show_loading_screen(registry)

    # Only render main UI if agent is ready
    if not agent_ready:
        st.error("⚠️ Could not connect to local agent. Please try restarting the UI.")
        st.markdown("### Troubleshooting:")
        st.markdown("- Check if `llauncher-agent` command is available")
        st.markdown("- Try running `llauncher-agent` manually in a separate terminal")
        return

    st.title("🚀 llauncher")
    st.markdown("Manage your llama.cpp servers across multiple nodes")

    # Sidebar
    with st.sidebar:
        st.header("Controls")

        # Refresh button
        if st.button("🔄 Refresh All", use_container_width=True):
            state.refresh()
            registry.refresh_all()
            st.rerun()

        st.divider()

        # Node selector
        st.subheader("🖥️ Node")

        # Build node options
        node_options = ["All Nodes"]
        for node in registry:
            status = "🟢" if node.status == NodeStatus.ONLINE else "⚫"
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
