"""UI delegation-path coverage for the model card (#200 review item 3).

``_handle_start`` (plain local launch) and ``_render_eviction_dialog``
(evict-and-start) both branch on ``delegation.should_delegate()``. #200's
acceptance criteria include "UI local-node launch → POST /start", so each
branch needs a unit test. These mirror ``TestDelegationRouting`` in
``tests/unit/mcp/test_servers_tools.py``: mock the local-agent-node factory
and assert the delegate branch POSTs via the node while the in-process
branch calls ``ops.*``.

Streamlit is mocked at the module seam (``model_card.st``); ``st.button``
returns True so the eviction "Confirm" path fires, and ``st.rerun`` is a
no-op (unlike real Streamlit it does not halt execution).
"""

from __future__ import annotations

from contextlib import contextmanager
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

        # Surfaced as an error toast, not an AttributeError.
        st.error.assert_called_once()


# ───────────────────────────── _handle_stop ─────────────────────────────────


class TestHandleStopDelegation:
    """A stop is mutating, so it must route through the delegation gate like
    start/swap (#200/#203). Mirrors ``TestHandleStartDelegation``: delegate →
    POST via the local agent node and never touch the in-process path; no agent
    → in-process ``state.stop_server``; ``None`` delegated result → failure
    toast, not an ``AttributeError`` (the cross-uid SIGTERM bug, ADR-018)."""

    def test_local_stop_delegates_over_http(self):
        node = MagicMock()
        node.stop_server.return_value = {"success": True, "message": "stopped"}
        state = MagicMock()

        with patch.object(model_card, "st", _mock_st()), _delegate(node):
            model_card._handle_stop(state, None, "local", 8080)

        node.stop_server.assert_called_once_with(8080)
        state.stop_server.assert_not_called()

    def test_local_stop_in_process_when_no_agent(self):
        state = MagicMock()
        state.stop_server.return_value = (True, "stopped")

        with patch.object(model_card, "st", _mock_st()), _delegate(
            MagicMock(), enabled=False
        ) as factory:
            model_card._handle_stop(state, None, "local", 8080)

        state.stop_server.assert_called_once_with(8080, caller="ui")
        factory.assert_not_called()

    def test_local_stop_none_result_is_safe(self):
        """A ``None`` delegated result must surface as failure, not raise."""
        node = MagicMock()
        node.stop_server.return_value = None
        state = MagicMock()

        with patch.object(model_card, "st", _mock_st()) as st, _delegate(node):
            model_card._handle_stop(state, None, "local", 8080)

        state.stop_server.assert_not_called()
        # Surfaced as a failure toast (icon="❌"), not an AttributeError.
        st.toast.assert_called_with("Local agent returned no result", icon="❌")


# ──────────────────────── _render_eviction_dialog (swap) ────────────────────


class TestEvictionDialogDelegation:
    def _state(self):
        state = MagicMock()
        state.running = {}  # existing_model lookup → "unknown"
        return state

    def test_eviction_delegates_over_http(self):
        node = MagicMock()
        node.swap_server.return_value = {"success": True}

        with patch.object(model_card, "st", _mock_st()), patch.object(
            model_card.ops, "swap"
        ) as mock_ops_swap, _delegate(node):
            model_card._render_eviction_dialog(self._state(), "local", 8080, "m", "")

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
            model_card._render_eviction_dialog(self._state(), "local", 8080, "m", "")

        mock_ops_swap.assert_called_once_with("m", 8080, caller="ui")
        factory.assert_not_called()

    def test_eviction_none_result_is_safe(self):
        node = MagicMock()
        node.swap_server.return_value = None

        with patch.object(model_card, "st", _mock_st()) as st, patch.object(
            model_card.ops, "swap"
        ), _delegate(node):
            model_card._render_eviction_dialog(self._state(), "local", 8080, "m", "")

        # Falls through to the failure toast without raising.
        st.toast.assert_called()
