"""Node management tab for multi-node llauncher."""

import subprocess
import time

import streamlit as st

from llauncher.remote.registry import NodeRegistry
from llauncher.remote.node import NodeStatus


def render_nodes_tab(registry: NodeRegistry, aggregator) -> None:
    """Render the nodes management tab.

    Args:
        registry: NodeRegistry instance.
        aggregator: RemoteAggregator instance.
    """
    st.header("🖥️ Nodes")
    st.markdown("Manage remote nodes running llauncher agents")

    # Show current nodes
    if registry:
        render_node_list(registry, aggregator)

    st.divider()

    # Add new node form
    with st.expander("➕ Add New Node", expanded=False):
        render_add_node_form(registry)


def render_node_list(registry: NodeRegistry, aggregator) -> None:
    """Render list of registered nodes.

    Args:
        registry: NodeRegistry instance.
        aggregator: RemoteAggregator instance.
    """
    st.subheader("Registered Nodes")

    if not registry:
        st.info("No nodes registered yet. Add a node using the form below.")
        return

    # Refresh button
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("🔄 Refresh All", use_container_width=True, key="refresh_all_nodes"):
            registry.refresh_all()
            st.toast("Refreshed all nodes", icon="🔄")
            st.rerun()

    # Node cards
    for node in registry:
        # Status badge
        if node.status == NodeStatus.ONLINE:
            status_icon = "🟢"
            status_label = "Online"
        elif node.status == NodeStatus.ERROR:
            status_icon = "🔴"
            status_label = "Error"
        else:
            status_icon = "⚫"
            status_label = "Offline"

        with st.expander(
            f"**{node.name}** {status_icon} ({node.host}:{node.port})",
            expanded=False,
        ):
            # Node info
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Host**")
                st.markdown(f"**Port**")
                st.markdown(f"**Status**")
            with col2:
                st.markdown(f"`{node.host}`")
                st.markdown(f"`{node.port}`")
                st.markdown(f"{status_label}")
                if node.last_seen:
                    st.markdown(f"*Last seen: {node.last_seen.strftime('%H:%M:%S')}*")

            # Try to get more info
            if node.status == NodeStatus.ONLINE:
                node_info = node.get_node_info()
                if node_info:
                    st.divider()
                    info_col1, info_col2 = st.columns(2)
                    with info_col1:
                        st.markdown("**OS**")
                        st.markdown("**Python**")
                    with info_col2:
                        st.markdown(f"`{node_info.get('os', 'N/A')}`")
                        st.markdown(f"`{node_info.get('python_version', 'N/A')}`")

                    # Show IP addresses
                    ips = node_info.get("ip_addresses", [])
                    if ips:
                        st.markdown("**IP Addresses**")
                        st.markdown(", ".join(f"`{ip}`" for ip in ips))

            # Show error if any
            if node._error_message:
                st.error(f"Error: {node._error_message}")

            st.divider()

            # Actions
            action_col1, action_col2 = st.columns(2)

            with action_col1:
                if st.button(
                    "🔍 Test Connection",
                    use_container_width=True,
                    key=f"test_{node.name}",
                ):
                    result = node.ping()
                    if result:
                        st.toast(
                            f"Connection successful! {node.name} is online.",
                            icon="✅"
                        )
                    else:
                        st.toast(
                            f"Connection failed: {node._error_message}",
                            icon="❌"
                        )
                    st.rerun()

            with action_col2:
                if node.name == "local":
                    # Local node is auto-managed and cannot be removed
                    st.info("Local node is auto-managed and cannot be removed")
                else:
                    if st.button(
                        "🗑️ Remove Node",
                        use_container_width=True,
                        key=f"remove_{node.name}",
                    ):
                        success, message = registry.remove_node(node.name)
                        if success:
                            st.success(message)
                        else:
                            st.error(message)
                        st.rerun()


def render_add_node_form(registry: NodeRegistry) -> None:
    """Render form to add a new node.

    Args:
        registry: NodeRegistry instance.
    """
    with st.form("add_node_form", clear_on_submit=True):
        node_name = st.text_input(
            "Node Name",
            help="Unique friendly name for this node (e.g., 'linux-box', 'windows-server')",
        )
        node_host = st.text_input(
            "Host",
            help="Hostname or IP address (e.g., '192.168.1.100' or 'server.local')",
        )
        col1, col2 = st.columns(2)
        with col1:
            node_port = st.number_input(
                "Port",
                min_value=1024,
                max_value=65535,
                value=8765,
                help="Port the llauncher agent is listening on",
            )
        with col2:
            timeout = st.number_input(
                "Timeout (seconds)",
                min_value=1,
                max_value=30,
                value=5,
                help="Connection timeout in seconds",
            )

        # Test connection button
        test_col, submit_col = st.columns(2)
        with test_col:
            test_clicked = st.form_submit_button(
                "🔍 Test Connection",
                use_container_width=True,
                type="secondary",
            )
        with submit_col:
            submit_clicked = st.form_submit_button(
                "➕ Add Node",
                use_container_width=True,
                type="primary",
            )

        if test_clicked:
            if not node_name or not node_host:
                st.error("Node name and host are required")
            else:
                from llauncher.remote.node import RemoteNode

                test_node = RemoteNode(node_name, node_host, node_port, timeout)
                result = test_node.ping()
                if result:
                    st.success(
                        f"Connection successful! Node '{node_name}' is online at {node_host}:{node_port}"
                    )
                    # Show node info
                    node_info = test_node.get_node_info()
                    if node_info:
                        st.info(
                            f"OS: {node_info.get('os', 'N/A')} "
                            f"| Python: {node_info.get('python_version', 'N/A')}"
                        )
                else:
                    st.error(
                        f"Connection failed: {test_node._error_message or 'Unknown error'}"
                    )

        if submit_clicked:
            if not node_name or not node_host:
                st.error("Node name and host are required")
                return

            success, message = registry.add_node(
                name=node_name,
                host=node_host,
                port=node_port,
                timeout=timeout,
                overwrite=True,
            )

            if success:
                st.success(message)
                # Test connection immediately
                node = registry.get_node(node_name)
                if node.ping():
                    st.success(f"Node '{node_name}' is online and ready!")
                else:
                    st.warning(
                        f"Node added but connection failed: {node._error_message}"
                    )
                st.rerun()
            else:
                st.error(message)


def check_and_prompt_local_agent(registry: NodeRegistry) -> None:
    """Check if local agent is running and prompt to start if not.

    Args:
        registry: NodeRegistry instance.
    """
    import socket

    # Check if agent is running on localhost
    local_node = registry.get_node("local")
    if local_node:
        if local_node.ping():
            return  # Local agent is running

    # Check if port 8765 is in use
    import socket as sock

    with sock.socket(sock.AF_INET, sock.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", 8765))
            return  # Something is running on the port
        except ConnectionRefusedError:
            pass

    # Prompt to start local agent
    if st.sidebar.button("🚀 Start Local Agent", use_container_width=True):
        try:
            # Start agent in background
            proc = subprocess.Popen(
                ["llauncher-agent"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            st.sidebar.success("Starting local agent...")
            time.sleep(2)

            # Add to registry if not present
            if not registry.get_node("local"):
                registry.add_node("local", "localhost", 8765, overwrite=True)
                st.rerun()

            # Test connection
            local_node = registry.get_node("local")
            if local_node and local_node.ping():
                st.sidebar.success("Local agent is running!")
            else:
                st.sidebar.error("Failed to start local agent")
        except Exception as e:
            st.sidebar.error(f"Failed to start agent: {e}")
