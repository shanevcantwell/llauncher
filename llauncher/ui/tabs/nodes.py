"""Node management tab for multi-node llauncher."""

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

    # Refresh button. #498: no explicit st.rerun() -- a click already
    # causes exactly one script rerun, and the node cards render below
    # this block in the same top-down pass, so they already read
    # `registry` freshly refreshed this run without a second execution.
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("🔄 Refresh All", width='stretch', key="refresh_all_nodes"):
            registry.refresh_all()
            st.toast("Refreshed all nodes", icon="🔄")

    # Node cards. #498: snapshotted via list() rather than iterating the
    # registry's live view directly -- the Remove Node action below
    # mutates the registry mid-loop now that its own st.rerun() (which
    # used to abort the run outright, sidestepping this) is gone. Without
    # the snapshot, removing a node while iterating registry.__iter__'s
    # live dict view would raise "dictionary changed size during
    # iteration" on the very next node.
    for node in list(registry):
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

            # Rotate API key (remote nodes only — the ``local`` entry
            # sources its token from ``~/.llauncher/agent.env`` via
            # NodeRegistry._populate_local_token; manually setting one
            # here would only create drift).
            if node.name != "local":
                with st.expander("🔑 Edit API key", expanded=False):
                    new_key = st.text_input(
                        "New API Key",
                        type="password",
                        key=f"edit_key_{node.name}",
                        help=(
                            "Replace this node's stored token. Persisted to "
                            "~/.llauncher/node_tokens.json (mode 0600). "
                            "Leave blank and save to clear."
                        ),
                    )
                    # #498: no explicit st.rerun() -- the success/error
                    # banner below renders inline, synchronously, in this
                    # same click's run; a click alone already causes
                    # exactly one script rerun.
                    if st.button(
                        "💾 Save token",
                        width='stretch',
                        key=f"save_key_{node.name}",
                    ):
                        # overwrite=True with all existing fields preserved.
                        ok, msg = registry.add_node(
                            name=node.name,
                            host=node.host,
                            port=node.port,
                            timeout=node.timeout,
                            api_key=new_key or None,
                            overwrite=True,
                        )
                        if ok:
                            st.success(
                                "Token cleared" if not new_key else "Token updated"
                            )
                        else:
                            st.error(msg)

            # Actions
            action_col1, action_col2 = st.columns(2)

            # #498: neither button below calls st.rerun() any more -- a
            # click already causes exactly one script rerun, and each
            # feedback call (toast / success / error) renders inline,
            # synchronously, right where the click is handled, in this
            # same run. Remove Node is safe to leave mid-loop now that
            # the node list above iterates a list() snapshot rather than
            # the registry's live view.
            with action_col1:
                if st.button(
                    "🔍 Test Connection",
                    width='stretch',
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

            with action_col2:
                if node.name == "local":
                    # Local node is auto-managed and cannot be removed
                    st.info("Local node is auto-managed and cannot be removed")
                else:
                    if st.button(
                        "🗑️ Remove Node",
                        width='stretch',
                        key=f"remove_{node.name}",
                    ):
                        success, message = registry.remove_node(node.name)
                        if success:
                            st.success(message)
                        else:
                            st.error(message)


def render_add_node_form(registry: NodeRegistry) -> None:
    """Render form to add a new node.

    Args:
        registry: NodeRegistry instance.
    """
    st.info(
        "Adding a remote node currently requires copying the remote agent's "
        "API token by hand. See the README section **Adding a remote node** "
        "for the step-by-step walkthrough. Automatic session-token issuance "
        "(#137) will eliminate this manual step in a future release.",
        icon="🔑",
    )

    with st.form("add_node_form", clear_on_submit=True):
        node_name = st.text_input(
            "Node Name",
            help="Unique friendly name for this node (e.g., 'linux-box', 'windows-server')",
        )
        node_host = st.text_input(
            "Host",
            help=(
                "Hostname or IP address only — no port (e.g., '192.168.1.100' "
                "or 'server.local'). Set the port separately below; "
                "'192.168.1.100:8765' will be rejected."
            ),
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

        api_key = st.text_input(
            "API Key",
            type="password",
            help=(
                "On the remote box, run `llauncher-agent print-token` (or "
                "read the `LLAUNCHER_AGENT_TOKEN=` line from "
                "`~/.llauncher/agent.env` on Linux / "
                "`$env:USERPROFILE\\.llauncher\\agent.env` on "
                "Windows) and paste the value here. Required for non-loopback "
                "agents (per ADR-LLNCH-003); leave blank only for unauthenticated "
                "loopback agents."
            ),
        )

        # Test connection button
        test_col, submit_col = st.columns(2)
        with test_col:
            test_clicked = st.form_submit_button(
                "🔍 Test Connection",
                width='stretch',
                type="secondary",
            )
        with submit_col:
            submit_clicked = st.form_submit_button(
                "➕ Add Node",
                width='stretch',
                type="primary",
            )

        if test_clicked:
            if not node_name or not node_host:
                st.error("Node name and host are required")
            else:
                from llauncher.remote.node import RemoteNode

                try:
                    test_node = RemoteNode(
                        node_name,
                        node_host,
                        node_port,
                        timeout,
                        api_key=api_key or None,
                    )
                except ValueError as e:
                    st.error(str(e))
                    return
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
                api_key=api_key or None,
                overwrite=True,
            )

            if success:
                # #498: no explicit st.rerun() -- the confirmation banners
                # below render inline, synchronously, in this same
                # submit's run. The new node itself will appear in the
                # node list above on the *next* rerun (that list already
                # rendered, earlier in this same script pass, before this
                # form) rather than this one -- an acceptable one-click
                # lag against the double-run this fix removes.
                st.success(message)
                # Test connection immediately
                node = registry.get_node(node_name)
                if node.ping():
                    st.success(f"Node '{node_name}' is online and ready!")
                else:
                    st.warning(
                        f"Node added but connection failed: {node._error_message}"
                    )
            else:
                st.error(message)


# ``check_and_prompt_local_agent`` was removed in M4 Slice 12 (issue #49).
# It originally hosted the "Start Local Agent" sidebar button; after that
# button's removal the function had zero callers and the agent-down case
# is now surfaced by :func:`llauncher.ui.app.show_agent_down_banner`
# before any tab renders.
