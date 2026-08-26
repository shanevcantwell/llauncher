"""UI delegation-path coverage for the model card (#200 review item 3).

``_handle_start`` (plain local launch) and ``_render_eviction_dialog``
(evict-and-start) both branch on ``delegation.should_delegate()``. #200's
acceptance criteria include "UI local-node launch → POST /start", so each
branch needs a unit test. These mirror ``TestDelegationRouting`` in
``tests/unit/mcp/test_servers_tools.py``: mock the local-agent-node factory
and assert the delegate branch POSTs via the node while the in-process
branch calls ``ops.*``.

Streamlit is mocked at the module seam (``model_card.st``). Issue #498
moved the eviction dialog's dispatch logic out of
``_render_eviction_dialog`` (which now only renders the warning + wires
the Cancel/Confirm buttons' ``on_click``) and into a standalone
``_confirm_eviction`` callback; the eviction delegation tests below call
that callback directly rather than relying on a mocked ``st.button``
return value to fire it.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from llauncher.ui.tabs import model_card
from llauncher.operations.swap import SwapResult


def _mock_st():
    """A Streamlit stand-in with the methods the card paths touch."""
    st = MagicMock()
    st.columns.side_effect = lambda n: [MagicMock(), MagicMock()]
    st.button.return_value = True  # fire button-gated branches
    return st


@contextmanager
def _delegate(node, *, enabled=True):
    """Patch the gate decision and the local-agent-node factory."""
    with patch.object(
        model_card.delegation, "should_delegate", return_value=enabled
    ), patch.object(
        model_card, "local_agent_node", return_value=node
    ) as factory:
        yield factory


# ───────────────────────── _handle_start (plain launch) ─────────────────────


class TestHandleStartDelegation:
    def _state(self):
        state = MagicMock()
        state.models = {"m": MagicMock()}
        state.can_start.return_value = (True, "")
        return state

    def test_local_start_delegates_over_http(self):
        node = MagicMock()
        node.start_server.return_value = {"success": True, "message": "ok"}
        empty_temp = MagicMock()
        empty_temp.running = {}  # target port not occupied → plain start path

        with patch.object(model_card, "st", _mock_st()), patch.object(
            model_card, "LauncherState", return_value=empty_temp
        ), patch.object(model_card.ops, "start") as mock_ops_start, _delegate(node):
            model_card._handle_start(
                self._state(), None, "local", "m", target_port=8080
            )

        node.start_server.assert_called_once_with("m", 8080)
        mock_ops_start.assert_not_called()

    def test_local_start_in_process_when_no_agent(self):
        empty_temp = MagicMock()
        empty_temp.running = {}
        result = MagicMock(success=True, message="started")

        with patch.object(model_card, "st", _mock_st()), patch.object(
            model_card, "LauncherState", return_value=empty_temp
        ), patch.object(
            model_card.ops, "start", return_value=result
        ) as mock_ops_start, _delegate(MagicMock(), enabled=False) as factory:
            model_card._handle_start(
                self._state(), None, "local", "m", target_port=8080
            )

        mock_ops_start.assert_called_once_with("m", 8080, caller="ui")
        factory.assert_not_called()

    def test_local_start_none_result_is_safe(self):
        """A ``None`` delegated result must not raise (dict | None seam)."""
        node = MagicMock()
        node.start_server.return_value = None
        empty_temp = MagicMock()
        empty_temp.running = {}

        with patch.object(model_card, "st", _mock_st()) as st, patch.object(
            model_card, "LauncherState", return_value=empty_temp
        ), patch.object(model_card.ops, "start"), _delegate(node):
            model_card._handle_start(
                self._state(), None, "local", "m", target_port=8080
            )

        # Surfaced as a failure toast, not an AttributeError. #401: the
        # message itself is no longer an immediate st.error() call here —
        # it is persisted to session_state (keyed per node/model) and
        # rendered sticky by _render_start_error on the next pass, so it
        # survives _handle_start's own trailing st.rerun() instead of being
        # wiped by it.
        st.toast.assert_called_once_with(
            "Local agent returned no result", icon="❌"
        )
        st.session_state.__setitem__.assert_any_call(
            model_card._start_error_key("local", "m"),
            "Local agent returned no result",
        )


# ───────────────────────────── _handle_stop ─────────────────────────────────


class TestHandleStopDelegation:
    """A stop is mutating, so it must route through the delegation gate like
    start/swap (#200/#203). Mirrors ``TestHandleStartDelegation``: delegate →
    POST via the local agent node and never touch the in-process path; no agent
    → in-process ``ops.stop`` (#332: the legacy ``state.stop_server`` fallback
    skipped lockfile removal and durable audit — replaced with the same
    ``operations.stop`` seam CLI/MCP already use); ``None`` delegated result →
    failure toast, not an ``AttributeError`` (the cross-uid SIGTERM bug,
    ADR-LLNCH-018).

    ``_handle_stop`` takes no port: as an ``on_click`` callback it re-resolves
    the live port from ``model_name`` (see ``_resolve_stop_port``), so these
    states seed ``state.running`` with the server the card is showing."""

    @staticmethod
    def _running_state(model_name="m", port=8080):
        state = MagicMock()
        state.running = {port: SimpleNamespace(config_name=model_name)}
        return state

    def test_local_stop_delegates_over_http(self):
        node = MagicMock()
        node.stop_server.return_value = {"success": True, "message": "stopped"}
        state = self._running_state()

        with patch.object(model_card, "st", _mock_st()), patch.object(
            model_card.ops, "stop"
        ) as mock_ops_stop, _delegate(node):
            model_card._handle_stop(state, None, "local", "m")

        node.stop_server.assert_called_once_with(8080)
        state.stop_server.assert_not_called()
        mock_ops_stop.assert_not_called()

    def test_local_stop_in_process_when_no_agent(self):
        state = self._running_state()
        result = MagicMock(success=True, message="stopped")

        with patch.object(model_card, "st", _mock_st()), patch.object(
            model_card.ops, "stop", return_value=result
        ) as mock_ops_stop, _delegate(MagicMock(), enabled=False) as factory:
            model_card._handle_stop(state, None, "local", "m")

        mock_ops_stop.assert_called_once_with(8080, caller="ui")
        state.stop_server.assert_not_called()
        factory.assert_not_called()

    def test_local_stop_none_result_is_safe(self):
        """A ``None`` delegated result must surface as failure, not raise."""
        node = MagicMock()
        node.stop_server.return_value = None
        state = self._running_state()

        with patch.object(model_card, "st", _mock_st()) as st, _delegate(node):
            model_card._handle_stop(state, None, "local", "m")

        state.stop_server.assert_not_called()
        # Surfaced as a failure toast (icon="❌"), not an AttributeError.
        st.toast.assert_called_with("Local agent returned no result", icon="❌")


# ──────────────────────── _render_eviction_dialog (swap) ────────────────────


class TestEvictionDialogDelegation:
    """Exercises ``_confirm_eviction`` (#498), which now owns the
    delegate-vs-in-process dispatch that ``_render_eviction_dialog``'s
    Confirm-button click branch used to inline before its own trailing
    ``st.rerun()`` was removed and the branch became an ``on_click``
    callback.

    ``flag_key`` is the real ``_eviction_flag_key`` string the dialog arms,
    not a placeholder: the callback writes ``st.session_state[flag_key]``,
    and a bare ``""`` would encode a key shape the product never produces.
    """

    FLAG_KEY = model_card._eviction_flag_key("local", 8080, "m")

    def test_eviction_delegates_over_http(self):
        node = MagicMock()
        node.swap_server.return_value = {"success": True}

        with patch.object(model_card, "st", _mock_st()), patch.object(
            model_card.ops, "swap"
        ) as mock_ops_swap, _delegate(node):
            model_card._confirm_eviction("local", 8080, "m", self.FLAG_KEY)

        node.swap_server.assert_called_once_with("m", 8080)
        mock_ops_swap.assert_not_called()

    def test_eviction_in_process_when_no_agent(self):
        envelope = SwapResult(
            success=True, action="swapped", port_state="serving",
            port=8080, model="m", previous_model="old",
        )
        with patch.object(model_card, "st", _mock_st()), patch.object(
            model_card.ops, "swap", return_value=envelope
        ) as mock_ops_swap, _delegate(MagicMock(), enabled=False) as factory:
            model_card._confirm_eviction("local", 8080, "m", self.FLAG_KEY)

        mock_ops_swap.assert_called_once_with("m", 8080, caller="ui")
        factory.assert_not_called()

    def test_eviction_none_result_is_safe(self):
        node = MagicMock()
        node.swap_server.return_value = None

        with patch.object(model_card, "st", _mock_st()) as st, patch.object(
            model_card.ops, "swap"
        ), _delegate(node):
            model_card._confirm_eviction("local", 8080, "m", self.FLAG_KEY)

        # Falls through to the failure toast without raising.
        st.toast.assert_called()


class TestHandleStopResolvesPortLive:
    """#498 review: the stop toggle must not act on a one-run-stale port.

    ``st.button``'s ``args`` are bound at render time, but the ``on_click``
    callback fires *before* the next run's ``state.refresh()``. A port freed
    and re-taken in between would have been stopped out from under its new
    occupant. ``_handle_stop`` therefore re-resolves the port by model name.
    """

    def test_local_stop_uses_the_models_current_port_not_the_rendered_one(self):
        """The card rendered while ``m`` held 8080; by click time it holds
        9000 (and 8080 belongs to another model). The stop must go to 9000.
        """
        state = MagicMock()
        state.running = {
            8080: SimpleNamespace(config_name="someone-else"),
            9000: SimpleNamespace(config_name="m"),
        }
        result = MagicMock(success=True, message="stopped")

        with patch.object(model_card, "st", _mock_st()), patch.object(
            model_card.ops, "stop", return_value=result
        ) as mock_ops_stop, _delegate(MagicMock(), enabled=False):
            model_card._handle_stop(state, None, "local", "m")

        mock_ops_stop.assert_called_once_with(9000, caller="ui")
        state.refresh.assert_called_once()

    def test_local_stop_of_a_vanished_server_dispatches_nothing(self):
        state = MagicMock()
        state.running = {8080: SimpleNamespace(config_name="someone-else")}

        with patch.object(model_card, "st", _mock_st()) as st, patch.object(
            model_card.ops, "stop"
        ) as mock_ops_stop, _delegate(MagicMock(), enabled=False) as factory:
            model_card._handle_stop(state, None, "local", "m")

        mock_ops_stop.assert_not_called()
        factory.assert_not_called()
        st.toast.assert_called_once_with("m is no longer running", icon="ℹ️")

    def test_remote_stop_uses_the_models_current_port_not_the_rendered_one(self):
        aggregator = MagicMock()
        aggregator.get_all_servers.return_value = [
            SimpleNamespace(node_name="gpu-rig", config_name="someone-else", port=8080),
            SimpleNamespace(node_name="gpu-rig", config_name="m", port=9000),
            SimpleNamespace(node_name="other-rig", config_name="m", port=7000),
        ]
        aggregator.stop_on_node.return_value = {"success": True, "message": "stopped"}

        with patch.object(model_card, "st", _mock_st()):
            model_card._handle_stop(MagicMock(), aggregator, "gpu-rig", "m")

        aggregator.stop_on_node.assert_called_once_with("gpu-rig", 9000)

    def test_remote_stop_of_a_vanished_server_dispatches_nothing(self):
        aggregator = MagicMock()
        aggregator.get_all_servers.return_value = []

        with patch.object(model_card, "st", _mock_st()) as st:
            model_card._handle_stop(MagicMock(), aggregator, "gpu-rig", "m")

        aggregator.stop_on_node.assert_not_called()
        st.toast.assert_called_once_with("m is no longer running", icon="ℹ️")

    def test_no_aggregator_still_reports_the_missing_connection(self):
        """The ``no connection to node`` branch predates the live re-resolve
        and must survive it -- there is no source to resolve a port from."""
        with patch.object(model_card, "st", _mock_st()) as st:
            model_card._handle_stop(MagicMock(), None, "gpu-rig", "m")

        st.toast.assert_called_once_with("Cannot stop: no connection to node", icon="❌")
