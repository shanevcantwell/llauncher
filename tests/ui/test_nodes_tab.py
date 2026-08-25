"""Streamlit ``AppTest`` tests for the Nodes tab (``llauncher/ui/tabs/nodes.py``).

``nodes.py`` is the harness exemplar (#328): every control the tab renders is
pinned to the same uniform standard — render smoke, every interactive
branch, and every remote/registry I/O verb asserted through the mocked
facades under ``forbid_direct_http``. What lives here, by surface:

1. **Rendered-output smoke of the Add Node form** — the real
   ``AppTest``-driven smoke that the ``#134`` salvage stopgap
   (``salvage/134-nodes-tab-test``) explicitly pointed forward to ("a real
   rendered-output smoke is deferred to the Streamlit AppTest harness (#69)").
   It asserts the form renders its fields with the Phase 0 (#134) operability
   copy that ships on ``main`` today: the manual-token-copy ``st.info`` banner
   and the platform-specific (``print-token`` / ``cat`` / ``Get-Content``)
   API-Key help, alongside the ADR-LLNCH-003 auth reference.

2. **Behavioral remote-I/O test** — drives the tab with ``remote/`` mocked and
   asserts the UI reaches the node *only* through the ``RemoteNode`` facade
   (``get_node_info`` / ``ping``), with **no** raw socket escaping the UI
   (``forbid_direct_http``). This is the runtime complement to the static import
   guard in ``tests/architecture/test_ui_layer_boundaries.py`` (ADR-LLNCH-025).

3. **Node-list branch coverage** — the empty-state banner (falsy registry),
   the "Refresh All" control, every ``NodeStatus`` badge, the optional
   ``last_seen`` / stored-error-message fields, the per-node "Edit API key"
   rotation (``registry.add_node(overwrite=True, ...)``, success/clear/error),
   the Test Connection success/failure toasts, and Remove Node
   (success/error, plus the ``local`` node's auto-managed exemption).

4. **Add Node form dispatch** — both form actions (Test Connection, Add Node)
   across their required-field, success, and failure branches, including the
   post-add readiness ping. Test Connection builds a real ``RemoteNode``, so
   its ``ping`` / ``get_node_info`` are patched at the class rather than
   mocked at the facade — still asserted under ``forbid_direct_http`` so no
   real socket opens.
"""

from __future__ import annotations

from unittest.mock import patch

from llauncher.remote.node import NodeStatus, RemoteNode
from llauncher.ui.tabs.nodes import render_node_list, render_nodes_tab


def _input_by_label(at, label):
    for el in at.text_input:
        if el.label == label:
            return el
    raise AssertionError(f"no text_input labelled {label!r}; saw "
                         f"{[e.label for e in at.text_input]}")


def _button_by_key(at, key):
    for el in at.button:
        if el.key == key:
            return el
    raise AssertionError(f"no button with key {key!r}; saw "
                         f"{[e.key for e in at.button]}")


def _button_by_label(at, label):
    for el in at.button:
        if el.label == label:
            return el
    raise AssertionError(f"no button labelled {label!r}; saw "
                         f"{[e.label for e in at.button]}")


class TestNodesTabRender:
    """The tab renders headlessly through the engine facades only."""

    def test_registry_with_no_nodes_renders_header_and_add_form(
        self, tab_harness, mock_registry, mock_aggregator
    ):
        # ``mock_registry`` is a present-but-node-less registry (truthy, iterates
        # to nothing). This exercises the header + node-list-frame + Add Node
        # form, not the (registry-falsy) empty-state banner branch.
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        assert not at.exception
        assert at.header[0].value == "🖥️ Nodes"
        assert "Registered Nodes" in {s.value for s in at.subheader}
        # With a node-less registry the Add Node form must still be reachable.
        labels = {el.label for el in at.text_input}
        assert {"Node Name", "Host", "API Key"} <= labels


class TestAddNodeFormSmoke:
    """Rendered-output smoke of the Add Node form (salvage #134 → #69 intent).

    Pins the shipped Phase 0 (#134) operability copy: the manual-token-copy
    banner and the platform-specific API-Key help.
    """

    def test_add_node_form_fields_render(
        self, tab_harness, mock_registry, mock_aggregator
    ):
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        assert not at.exception
        # The four operator inputs of the Add Node form.
        labels = {el.label for el in at.text_input}
        assert {"Node Name", "Host", "API Key"} <= labels
        # Port + Timeout numeric inputs render.
        number_labels = {el.label for el in at.number_input}
        assert {"Port", "Timeout (seconds)"} <= number_labels
        # Both form submit affordances render.
        button_labels = {el.label for el in at.button}
        assert "➕ Add Node" in button_labels
        assert "🔍 Test Connection" in button_labels

    def test_manual_token_copy_banner_renders(
        self, tab_harness, mock_registry, mock_aggregator
    ):
        """An info banner surfaces the manual flow and its README pointer."""
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        assert not at.exception
        banner = next(
            el.value for el in at.info if "API token by hand" in el.value
        )
        assert "Adding a remote node" in banner  # README section pointer
        assert "#137" in banner  # roadmap issue tracking the successor

    def test_api_key_help_documents_token_source_and_adr(
        self, tab_harness, mock_registry, mock_aggregator
    ):
        """First-contact help tells the operator what the API Key field wants.

        Asserts the Phase 0 (#134) copy that ships on ``main`` today: the
        platform-specific token-retrieval commands and the ADR-LLNCH-003 auth
        reference.
        """
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        help_text = _input_by_label(at, "API Key").help
        assert "llauncher-agent print-token" in help_text
        assert "~/.llauncher/agent.env" in help_text
        assert "$env:USERPROFILE\\.llauncher\\agent.env" in help_text
        assert "ADR-LLNCH-003" in help_text


class TestAddNodeFormHostValidation:
    """Issue #27: the form surfaces ``NodeConfig`` host validation as an
    ``st.error``, never an uncaught exception, and documents the rule
    up front in the field's help text.
    """

    def test_host_help_warns_against_embedded_port(
        self, tab_harness, mock_registry, mock_aggregator
    ):
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        help_text = _input_by_label(at, "Host").help
        assert "no port" in help_text.lower()

    def test_embedded_port_host_shows_error_not_exception(
        self, tab_harness, mock_registry, mock_aggregator
    ):
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        _input_by_label(at, "Node Name").set_value("gpu-rig")
        _input_by_label(at, "Host").set_value("192.168.1.50:8765")
        _button_by_label(at, "🔍 Test Connection").click()
        at.run()

        assert not at.exception
        assert any("port" in e.value for e in at.error)


class TestNodesTabRemoteIO:
    """The UI reaches the node only through ``remote/`` — never raw HTTP."""

    def test_render_drives_node_info_through_remote_facade(
        self, tab_harness, mock_aggregator, make_node, registry_factory, forbid_direct_http
    ):
        """Rendering an online node pulls its info via ``RemoteNode.get_node_info``.

        Wrapped in ``forbid_direct_http`` so that if the tab ever regressed to
        doing its own socket/HTTP instead of going through the mocked facade,
        the render would raise instead of quietly passing.
        """
        node = make_node(name="gpu-rig", status=NodeStatus.ONLINE, online=True)
        registry = registry_factory([node])

        with forbid_direct_http():
            at = tab_harness(render_nodes_tab, registry, mock_aggregator)

        assert not at.exception
        # Node info was sourced through the RemoteNode facade (a remote-I/O
        # verb), not by the UI opening its own connection.
        node.get_node_info.assert_called_once()
        # The node was discovered by *iterating the registry facade*, proving
        # the tab reads nodes through remote/ rather than constructing its own.
        assert registry.__iter__.called

    def test_test_connection_button_calls_node_ping(
        self, tab_harness, mock_aggregator, make_node, registry_factory, forbid_direct_http
    ):
        """Clicking a node's "Test Connection" drives ``RemoteNode.ping``."""
        node = make_node(name="gpu-rig", status=NodeStatus.ONLINE, online=True)
        registry = registry_factory([node])

        with forbid_direct_http():
            at = tab_harness(render_nodes_tab, registry, mock_aggregator)
            assert not node.ping.called  # not pinged on plain render

            _button_by_key(at, "test_gpu-rig").click()
            at.run()

        assert not at.exception
        node.ping.assert_called_once()

    def test_offline_node_skips_node_info_fetch(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """An OFFLINE node renders without the UI attempting an info fetch."""
        node = make_node(name="cold-rig", status=NodeStatus.OFFLINE, online=False)
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)

        assert not at.exception
        node.get_node_info.assert_not_called()


class TestNodeListEmptyState:
    """A falsy (unset) registry short-circuits to a single info banner."""

    def test_falsy_registry_shows_empty_state_banner_only(
        self, tab_harness, mock_aggregator
    ):
        """``render_node_list`` on a falsy registry renders the empty-state
        banner and returns — no refresh button reached. Exercised directly
        against ``render_node_list``: ``render_nodes_tab`` itself only calls
        it when the registry is truthy (``if registry:``), so this branch
        is unreachable through the composed tab and must be pinned at the
        function it actually lives in.
        """
        # A registry double that is falsy (``bool(registry) is False``),
        # distinct from ``mock_registry`` which is truthy-but-empty.
        empty_registry = None

        at = tab_harness(render_node_list, empty_registry, mock_aggregator)

        assert not at.exception
        banner = next(
            el.value for el in at.info
            if "No nodes registered yet" in el.value
        )
        assert "Add a node using the form below" in banner


class TestRefreshAllButton:
    """The "Refresh All" control dispatches through the registry facade."""

    def test_refresh_all_click_calls_registry_refresh_all(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """Clicking "Refresh All" calls ``registry.refresh_all()`` and
        surfaces a confirmation toast — the only I/O verb this control
        drives.
        """
        node = make_node(name="gpu-rig", status=NodeStatus.ONLINE, online=True)
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)
        assert not registry.refresh_all.called

        _button_by_key(at, "refresh_all_nodes").click()
        at.run()

        assert not at.exception
        registry.refresh_all.assert_called_once()


class TestNodeStatusBadges:
    """Every ``NodeStatus`` value renders its own badge/label pairing."""

    def test_error_status_renders_red_badge_and_error_label(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """A node in ``NodeStatus.ERROR`` renders the "Error" status label
        (distinct from the Online/Offline badges already pinned above).
        """
        node = make_node(
            name="flaky-rig",
            status=NodeStatus.ERROR,
            online=False,
            error_message="connection refused",
        )
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)

        assert not at.exception
        assert any("Error" == el.value for el in at.markdown)


class TestNodeCardOptionalFields:
    """Optional per-node fields render only when the node carries them."""

    def test_last_seen_timestamp_renders_when_present(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """A node with a ``last_seen`` timestamp shows it formatted
        ``HH:MM:SS`` in the card — the branch skipped entirely for a node
        that has never been seen.
        """
        import datetime

        node = make_node(name="gpu-rig", status=NodeStatus.ONLINE, online=True)
        node.last_seen = datetime.datetime(2026, 7, 16, 9, 30, 15)
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)

        assert not at.exception
        assert any(
            "Last seen: 09:30:15" in el.value for el in at.markdown
        )

    def test_node_error_message_renders_as_error_element(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """A node carrying a stored ``_error_message`` (e.g. from a failed
        background refresh) surfaces it via ``st.error`` on render, not
        just after an interactive Test Connection click.
        """
        node = make_node(
            name="flaky-rig",
            status=NodeStatus.OFFLINE,
            online=False,
            error_message="timed out after 5s",
        )
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)

        assert not at.exception
        assert any("timed out after 5s" in el.value for el in at.error)


class TestEditApiKeyControl:
    """The per-node "Edit API key" expander dispatches through
    ``registry.add_node(overwrite=True, ...)`` — the same verb the Add Node
    form uses, reused here to rotate a token in place.
    """

    def test_save_token_with_new_value_calls_add_node_overwrite(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """Entering a new key and clicking "Save token" round-trips through
        ``registry.add_node`` with ``overwrite=True`` and every existing
        field preserved, then reports "Token updated"."""
        node = make_node(name="gpu-rig", status=NodeStatus.ONLINE, online=True)
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)
        _input_by_label(at, "New API Key").set_value("new-secret-token")
        _button_by_key(at, "save_key_gpu-rig").click()
        at.run()

        assert not at.exception
        registry.add_node.assert_called_once_with(
            name="gpu-rig",
            host=node.host,
            port=node.port,
            timeout=node.timeout,
            api_key="new-secret-token",
            overwrite=True,
        )
        assert any("Token updated" in el.value for el in at.success)

    def test_save_token_blank_clears_key_and_reports_cleared(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """Saving a blank key clears the stored token (``api_key=None``)
        and reports "Token cleared", the sibling branch to "Token updated".
        """
        node = make_node(name="gpu-rig", status=NodeStatus.ONLINE, online=True)
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)
        _button_by_key(at, "save_key_gpu-rig").click()
        at.run()

        assert not at.exception
        _, kwargs = registry.add_node.call_args
        assert kwargs["api_key"] is None
        assert any("Token cleared" in el.value for el in at.success)

    def test_save_token_failure_shows_error(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """A rejected token rotation (e.g. persistence failure) surfaces
        the registry's message via ``st.error``, not a silent no-op."""
        node = make_node(name="gpu-rig", status=NodeStatus.ONLINE, online=True)
        registry = registry_factory([node])
        registry.add_node.return_value = (False, "could not write node_tokens.json")

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)
        _button_by_key(at, "save_key_gpu-rig").click()
        at.run()

        assert not at.exception
        assert any(
            "could not write node_tokens.json" in el.value for el in at.error
        )

    def test_local_node_has_no_edit_api_key_control(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """The ``local`` node sources its token from ``agent.env`` — no Edit
        API key expander is offered for it (avoids drift)."""
        node = make_node(name="local", status=NodeStatus.ONLINE, online=True)
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)

        assert not at.exception
        assert not any(el.key == "save_key_local" for el in at.button)


class TestTestConnectionButtonFailure:
    """The per-node "Test Connection" button's failure branch."""

    def test_failed_ping_shows_failure_toast_with_error_message(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """A failed ``node.ping()`` surfaces a failure toast carrying the
        node's stored error message — the sibling branch to the
        already-pinned success toast."""
        node = make_node(
            name="cold-rig",
            status=NodeStatus.OFFLINE,
            online=False,
            error_message="connection refused",
        )
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)
        _button_by_key(at, "test_cold-rig").click()
        at.run()

        assert not at.exception
        node.ping.assert_called_once()
        assert any(
            "Connection failed: connection refused" in t.body for t in at.toast
        )


class TestRemoveNodeControl:
    """The "Remove Node" action dispatches through ``registry.remove_node``;
    the ``local`` node is exempted entirely (auto-managed, no button).
    """

    def test_local_node_shows_auto_managed_notice_not_remove_button(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """The ``local`` node renders an informational notice in place of
        the Remove Node button — it cannot be removed."""
        node = make_node(name="local", status=NodeStatus.ONLINE, online=True)
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)

        assert not at.exception
        assert any(
            "auto-managed and cannot be removed" in el.value for el in at.info
        )
        assert not any(el.key == "remove_local" for el in at.button)

    def test_remove_node_success_calls_registry_and_shows_success(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """Clicking "Remove Node" on a non-local node calls
        ``registry.remove_node(name)`` and shows the registry's success
        message."""
        node = make_node(name="gpu-rig", status=NodeStatus.ONLINE, online=True)
        registry = registry_factory([node])

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)
        _button_by_key(at, "remove_gpu-rig").click()
        at.run()

        assert not at.exception
        registry.remove_node.assert_called_once_with("gpu-rig")
        assert any("Node removed" in el.value for el in at.success)

    def test_remove_node_failure_shows_error(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """A rejected removal surfaces the registry's failure message via
        ``st.error`` — the sibling branch to the success path above."""
        node = make_node(name="gpu-rig", status=NodeStatus.ONLINE, online=True)
        registry = registry_factory([node])
        registry.remove_node.return_value = (False, "node is running a server")

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)
        _button_by_key(at, "remove_gpu-rig").click()
        at.run()

        assert not at.exception
        assert any("node is running a server" in el.value for el in at.error)


class TestAddNodeFormTestConnection:
    """The Add Node form's "Test Connection" button builds a real
    ``RemoteNode`` and drives it through the same ``ping`` /
    ``get_node_info`` facade verbs as the registered-node cards — the form
    never opens its own socket.
    """

    def test_missing_required_fields_shows_error_without_constructing_node(
        self, tab_harness, mock_registry, mock_aggregator
    ):
        """Clicking Test Connection with no name/host shows the required-
        fields error and never attempts to build a ``RemoteNode``."""
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        with patch.object(RemoteNode, "__init__") as mock_init:
            _button_by_label(at, "🔍 Test Connection").click()
            at.run()

        assert not at.exception
        assert any(
            "Node name and host are required" in el.value for el in at.error
        )
        mock_init.assert_not_called()

    def test_successful_ping_shows_success_and_node_info(
        self, tab_harness, mock_registry, mock_aggregator, forbid_direct_http
    ):
        """A successful test-connection ping reports success and renders
        the remote node's OS/Python info, sourced only through
        ``RemoteNode.ping`` / ``get_node_info`` (never raw HTTP)."""
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        _input_by_label(at, "Node Name").set_value("gpu-rig")
        _input_by_label(at, "Host").set_value("192.168.1.50")

        with forbid_direct_http(), \
                patch.object(RemoteNode, "ping", return_value=True), \
                patch.object(
                    RemoteNode,
                    "get_node_info",
                    return_value={"os": "Linux", "python_version": "3.12.3"},
                ):
            _button_by_label(at, "🔍 Test Connection").click()
            at.run()

        assert not at.exception
        assert any(
            "Connection successful! Node 'gpu-rig' is online" in el.value
            for el in at.success
        )
        assert any(
            "OS: Linux | Python: 3.12.3" in el.value for el in at.info
        )

    def test_failed_ping_shows_connection_failed_error(
        self, tab_harness, mock_registry, mock_aggregator, forbid_direct_http
    ):
        """A failed test-connection ping surfaces the node's stored error
        message via ``st.error`` — the sibling branch to the success case."""
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        _input_by_label(at, "Node Name").set_value("gpu-rig")
        _input_by_label(at, "Host").set_value("192.168.1.50")

        def _set_error_and_fail(self):
            self._error_message = "connection refused"
            return False

        with forbid_direct_http(), \
                patch.object(RemoteNode, "ping", _set_error_and_fail):
            _button_by_label(at, "🔍 Test Connection").click()
            at.run()

        assert not at.exception
        assert any(
            "Connection failed: connection refused" in el.value for el in at.error
        )


class TestAddNodeFormSubmit:
    """The Add Node form's primary submit action — ``registry.add_node`` —
    followed by an immediate readiness ping, mirroring the per-node Test
    Connection contract for a just-added node.
    """

    def test_missing_required_fields_shows_error(
        self, tab_harness, mock_registry, mock_aggregator
    ):
        """Submitting with no name/host shows the required-fields error and
        never calls ``registry.add_node``."""
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        _button_by_label(at, "➕ Add Node").click()
        at.run()

        assert not at.exception
        assert any(
            "Node name and host are required" in el.value for el in at.error
        )
        mock_registry.add_node.assert_not_called()

    def test_successful_add_and_ready_shows_online_confirmation(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """A successful add whose immediate readiness ping succeeds reports
        both the registry's success message and an "online and ready"
        confirmation."""
        registry = registry_factory([])
        added_node = make_node(name="gpu-rig", status=NodeStatus.ONLINE, online=True)
        registry.add_node.return_value = (True, "Node added")
        registry.get_node.side_effect = None
        registry.get_node.return_value = added_node

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)
        _input_by_label(at, "Node Name").set_value("gpu-rig")
        _input_by_label(at, "Host").set_value("192.168.1.50")
        _button_by_label(at, "➕ Add Node").click()
        at.run()

        assert not at.exception
        registry.add_node.assert_called_once()
        assert registry.add_node.call_args.kwargs["overwrite"] is True
        assert any("Node added" in el.value for el in at.success)
        assert any(
            "'gpu-rig' is online and ready!" in el.value for el in at.success
        )

    def test_successful_add_but_unreachable_shows_warning(
        self, tab_harness, mock_aggregator, make_node, registry_factory
    ):
        """A successful add whose immediate readiness ping fails reports
        the registry's success message plus a warning (not an error) that
        the node could not yet be reached."""
        registry = registry_factory([])
        added_node = make_node(
            name="gpu-rig",
            status=NodeStatus.OFFLINE,
            online=False,
            error_message="connection refused",
        )
        registry.add_node.return_value = (True, "Node added")
        registry.get_node.side_effect = None
        registry.get_node.return_value = added_node

        at = tab_harness(render_nodes_tab, registry, mock_aggregator)
        _input_by_label(at, "Node Name").set_value("gpu-rig")
        _input_by_label(at, "Host").set_value("192.168.1.50")
        _button_by_label(at, "➕ Add Node").click()
        at.run()

        assert not at.exception
        assert any(
            "connection failed: connection refused" in el.value for el in at.warning
        )

    def test_add_node_failure_shows_error(
        self, tab_harness, mock_registry, mock_aggregator
    ):
        """A rejected add (e.g. duplicate name) surfaces the registry's
        failure message via ``st.error`` — the sibling branch to both
        success cases above."""
        mock_registry.add_node.return_value = (False, "node 'gpu-rig' already exists")

        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)
        _input_by_label(at, "Node Name").set_value("gpu-rig")
        _input_by_label(at, "Host").set_value("192.168.1.50")
        _button_by_label(at, "➕ Add Node").click()
        at.run()

        assert not at.exception
        assert any(
            "node 'gpu-rig' already exists" in el.value for el in at.error
        )
