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


def ensure_local_agent(registry: NodeRegistry) -> None:
    """Ensure local agent is running and registered."""
    import os
    import socket
    import sys

    # Use same default port as the agent config
    AGENT_PORT = int(os.getenv("LAUNCHER_AGENT_PORT", "8765"))

    # Check if local node exists and is online
    local_node = registry.get_node("local")
    if local_node and local_node.ping():
        return

    # Check if port is in use
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", AGENT_PORT))
            # Something is running - add to registry if not present
            if not local_node:
                registry.add_node("local", "localhost", AGENT_PORT, overwrite=True)
            return
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
        time.sleep(2)

        # Add to registry if not present
        if not registry.get_node("local"):
            registry.add_node("local", "localhost", AGENT_PORT, overwrite=True)

        # Verify it's running
        local_node = registry.get_node("local")
        if local_node and local_node.ping():
            st.session_state["local_agent_started"] = True
    except Exception:
        pass


def main():
    """Main entry point for the Streamlit app."""
    st.title("🚀 llauncher")
    st.markdown("Manage your llama.cpp servers across multiple nodes")

    # Get state and registry
    state = get_state()
    registry = get_registry()
    aggregator = get_aggregator()

    # Ensure local agent is running
    ensure_local_agent(registry)

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
