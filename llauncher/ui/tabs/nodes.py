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

    # Node cards. Remove Node dispatches from an ``on_click`` callback
    # (#498 review), so the registry mutation lands *before* this loop
    # starts -- there is no mid-loop mutation to guard against and no
    # snapshot to take, and the removed node's card never renders in the
    # first place.
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
            # click already causes exactly one script rerun. Both are
            # ``on_click`` callbacks rather than click branches, because
            # both invalidate output this loop has *already* drawn:
            # ping() rewrites node.status, which the badge above (and the
            # expander label) rendered from; remove_node() unregisters the
            # node whose card is mid-render. A callback runs before the
            # script body, so its effect is in place by the time anything
            # renders this run.
            with action_col1:
                st.button(
                    "🔍 Test Connection",
                    width='stretch',
                    key=f"test_{node.name}",
                    on_click=_ping_node,
                    args=(node,),
                )

            with action_col2:
                if node.name == "local":
                    # Local node is auto-managed and cannot be removed
                    st.info("Local node is auto-managed and cannot be removed")
                else:
                    st.button(
                        "🗑️ Remove Node",
                        width='stretch',
                        key=f"remove_{node.name}",
                        on_click=_remove_node,
                        args=(registry, node.name),
                    )


def _ping_node(node) -> None:
    """``on_click`` callback for a node card's Test Connection (#498).

    Runs before the script body so the status badge, the expander label
    and the ``last_seen`` line -- all of which this run renders from
    ``node.status`` *above* the button -- reflect the ping the operator
    just asked for, rather than the state it invalidated. A click branch
    could only toast the result while the page around it still showed the
    stale badge.

    ``st.toast`` from a callback is replayed into the run the callback
    precedes (verified with AppTest against streamlit 1.59.1), so the
    feedback still reaches the operator.
    """
    if node.ping():
        st.toast(f"Connection successful! {node.name} is online.", icon="✅")
    else:
        st.toast(f"Connection failed: {node._error_message}", icon="❌")


def _remove_node(registry: NodeRegistry, node_name: str) -> None:
    """``on_click`` callback for a node card's Remove Node (#498).

    Runs before the node loop, so the removed node is gone from the
    registry by the time any card renders -- as a click branch it left the
    doomed node's own fully-rendered card sitting on the page, and forced
    the loop to iterate a ``list()`` snapshot to survive mutating the
    registry mid-iteration. Both symptoms disappear with the callback.

    Feedback is a banner rather than a card-local message on purpose: the
    card it would have belonged to no longer exists.
    """
    success, message = registry.remove_node(node_name)
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
            key="add_node_name",
            help="Unique friendly name for this node (e.g., 'linux-box', 'windows-server')",
        )
        node_host = st.text_input(
            "Host",
            help=(
                "Hostname or IP address only — no port (e.g., '192.168.1.100' "
                "or 'server.local'). Set the port separately below; "
                "'192.168.1.100:8765' will be rejected."
            ),
            key="add_node_host",
        )
        col1, col2 = st.columns(2)
        with col1:
            node_port = st.number_input(
                "Port",
                min_value=1024,
                max_value=65535,
                value=8765,
                help="Port the llauncher agent is listening on",
                key="add_node_port",
            )
        with col2:
            timeout = st.number_input(
                "Timeout (seconds)",
                min_value=1,
                max_value=30,
                value=5,
                help="Connection timeout in seconds",
                key="add_node_timeout",
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
            key="add_node_api_key",
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
            # on_click (#498 review): the node list renders *above* this
            # form, so a click branch here registered the node too late
            # for it to appear this run -- the operator saw "Node added"
            # over a list that did not contain it. The callback runs
            # before the script body, so the new node is in the registry
            # by the time the list draws. The submitted values are read
            # from st.session_state by key inside the callback (Streamlit
            # applies a form's pending widget updates before invoking its
            # submit button's on_click), not from the locals above, which
            # still hold this run's pre-submit values -- the same idiom
            # forms.py::_save_edit_callback uses.
            st.form_submit_button(
                "➕ Add Node",
                width='stretch',
                type="primary",
                on_click=_add_node_callback,
                args=(registry,),
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


def _add_node_callback(registry: NodeRegistry) -> None:
    """``on_click`` callback for the Add Node form's submit button (#498).

    Registers the node before anything renders this run, so the node list
    above the form already includes it -- see the submit button's comment
    for why a click branch could not.

    Values are indexed, not ``.get()``-with-a-default: this callback can
    only fire from the form's own submit button and every ``add_node_*``
    widget renders unconditionally inside that form, so a missing key
    means the form and this reader have drifted apart -- fail loud
    (``PARSE-AT-THE-DOOR``) rather than silently registering a default.

    ``st.error``/``st.success``/``st.warning`` from a callback are
    replayed into the run the callback precedes (verified with AppTest
    against streamlit 1.59.1), so the banners still reach the operator.
    """
    node_name = st.session_state["add_node_name"]
    node_host = st.session_state["add_node_host"]

    if not node_name or not node_host:
        st.error("Node name and host are required")
        return

    success, message = registry.add_node(
        name=node_name,
        host=node_host,
        port=st.session_state["add_node_port"],
        timeout=st.session_state["add_node_timeout"],
        api_key=st.session_state["add_node_api_key"] or None,
        overwrite=True,
    )

    if not success:
        st.error(message)
        return

    st.success(message)
    # Test connection immediately
    node = registry.get_node(node_name)
    if node.ping():
        st.success(f"Node '{node_name}' is online and ready!")
    else:
        st.warning(f"Node added but connection failed: {node._error_message}")


# ``check_and_prompt_local_agent`` was removed in M4 Slice 12 (issue #49).
# It originally hosted the "Start Local Agent" sidebar button; after that
# button's removal the function had zero callers and the agent-down case
# is now surfaced by :func:`llauncher.ui.app.show_agent_down_banner`
# before any tab renders.
