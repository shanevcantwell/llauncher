"""Streamlit ``AppTest`` tests for the Nodes tab (``llauncher/ui/tabs/nodes.py``).

Two things live here:

1. **Rendered-output smoke of the Add Node form** — the real
   ``AppTest``-driven smoke that the ``#134`` salvage stopgap
   (``salvage/134-nodes-tab-test``) explicitly pointed forward to ("a real
   rendered-output smoke is deferred to the Streamlit AppTest harness (#69)").
   It asserts the form renders its fields with the help text that *actually
   ships* today (ADR-003 / ``LLAUNCHER_AGENT_TOKEN``).

   Note on the salvage: that stopgap also pinned a manual-token-copy ``st.info``
   banner and platform-specific (``cat`` / ``Get-Content``) API-Key help keyed
   to issue ``#135``. That copy was never merged into ``render_add_node_form``
   on ``main`` — it remains terser. Re-implementing those exact assertions would
   require *adding speculative UI copy that references unshipped #135 work*,
   which is a product decision, not a test salvage. So this file preserves the
   salvage's intent (a rendered smoke of the Add Node form) against the shipped
   copy and surfaces the stranded banner copy in the PR rather than fabricating
   it.

2. **Behavioral remote-I/O test** — drives the tab with ``remote/`` mocked and
   asserts the UI reaches the node *only* through the ``RemoteNode`` facade
   (``get_node_info`` / ``ping``), with **no** raw socket escaping the UI
   (``forbid_direct_http``). This is the runtime complement to the static import
   guard in ``tests/architecture/test_ui_layer_boundaries.py`` (ADR-025).
"""

from __future__ import annotations

from llauncher.remote.node import NodeStatus
from llauncher.ui.tabs.nodes import render_nodes_tab


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

    Pins the *shipped* operability copy, not the never-merged #134 banner.
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

    def test_api_key_help_documents_token_source_and_adr(
        self, tab_harness, mock_registry, mock_aggregator
    ):
        """First-contact help tells the operator what the API Key field wants.

        Asserts the copy that ships on ``main`` today — the agent-token source
        and the ADR-003 auth reference — rather than the stranded #134 banner.
        """
        at = tab_harness(render_nodes_tab, mock_registry, mock_aggregator)

        help_text = _input_by_label(at, "API Key").help
        assert "LLAUNCHER_AGENT_TOKEN" in help_text
        assert "agent.token" in help_text
        assert "ADR-003" in help_text


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
