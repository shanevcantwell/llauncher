"""Streamlit ``AppTest`` tests for the model card
(``llauncher/ui/tabs/model_card.py``, SP-6, #328).

## The pinned surface contract

The model card is the UI's **verb-dispatch surface**. Per the #330 parity
matrix, every mutating verb leaves the card through exactly one of two doors:

* **Delegated** — when ``core.delegation.should_delegate()`` is truthy, the
  verb POSTs to the local agent via ``local_agent_node().<verb>_server(...)``
  (#200/#203); no in-process spawn/stop happens in the UI process.
* **In-process** — otherwise the verb dispatches its ``llauncher.operations``
  twin with the surface's caller tag: ``ops.start(name, port, caller="ui")``,
  ``ops.stop(port, caller="ui")``, ``ops.swap(name, port, caller="ui")``.
  Since #332 / PR #344 this includes **stop** — the legacy
  ``state.stop_server`` path is gone, and these tests pin that it stays gone.

``delete`` is the ratified ungated exception: all four surfaces call
``ops.delete_model(name, caller=<surface>)`` directly, behind a two-step
confirm gate in the UI. Remote-node cards dispatch through the
``RemoteAggregator`` facade (``start_on_node`` / ``stop_on_node`` /
``get_logs_on_node``) and never through ``ops`` or the agent factory.

Every test here asserts that seam — which orchestration call fired, with what
arguments — never widget cosmetics. UI-only pre-dispatch authority (the
occupied-port check, now reading ``state.running`` directly per #392, and
the ``state.can_start`` gate) is pinned **as it ships today**; its redesign
is banked as #333 and will re-pin these tests.

Idiom: ``_click_and_run`` — a card control's click only lands on the next
script run, and most handlers end in ``st.rerun()``, which ``at.run()`` folds
into the same call (the rerun discipline shared by all 7+ rerun sites here).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llauncher.remote.node import RemoteServerInfo
from llauncher.ui.tabs.model_card import (
    _eviction_flag_key,
    _render_eviction_dialog,
    render_model_card,
)

MODEL = "test-model"
PORT = 8123
PID = 4242


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _click_and_run(at, key):
    """Click the button with widget key ``key``, then run the script.

    The rerun discipline: a click registers on the *next* run, and the
    handlers' trailing ``st.rerun()`` is folded into the same ``at.run()``
    call by AppTest — so one click + one run lands the whole interaction.
    """
    at.button(key=key).click()
    at.run()
    return at


def _set_port(at, port=PORT, *, node_name="local", model_name=MODEL):
    """Type a port into the card's ADR-010 port picker (no run)."""
    at.number_input(key=f"start_{node_name}_{model_name}_port").set_value(port)


def _server(node_name="local", port=PORT, config_name=MODEL, pid=PID):
    return RemoteServerInfo(
        node_name=node_name,
        pid=pid,
        port=port,
        config_name=config_name,
        start_time="2026-07-16T00:00:00+00:00",
        uptime_seconds=61,
    )


def _card(tab_harness, state, aggregator, node_name, model, running=None):
    return tab_harness(
        render_model_card, state, None, aggregator, node_name, model, running
    )


# ---------------------------------------------------------------------------
# Fixtures local to this surface (the shared dispatch-seam doubles —
# mock_ops / mock_should_delegate / mock_local_agent_node — live in
# conftest.py, owned by this work item).
# ---------------------------------------------------------------------------
@pytest.fixture
def model_dict():
    return {"name": MODEL, "model_path": "/models/test-model.gguf", "n_gpu_layers": 35}


@pytest.fixture
def card_state(mock_state):
    """``mock_state`` specialised for the card: config present, gate open."""
    mock_state.models[MODEL] = MagicMock(name="ModelConfig")
    mock_state.can_start.return_value = (True, "")
    return mock_state


@pytest.fixture
def mock_occupancy(card_state):
    """The occupied-port check's data source (issue #392).

    Historically ``_handle_start`` consulted a *fresh* ``LauncherState()``
    argv-scan rather than the passed-in ``state`` — a redundant
    ``psutil.process_iter`` scan removed in #392 with zero behavior change:
    the check now reads ``state.running`` directly, same as
    ``_render_eviction_dialog`` already did. This fixture is kept (aliased
    to ``card_state``) so existing tests can still seed
    ``mock_occupancy.running[port]`` to drive the start->eviction reroute.
    """
    return card_state


@pytest.fixture
def mock_stream_logs():
    """Patch the card's local log read (``core.process.stream_logs``)."""
    with patch(
        "llauncher.ui.tabs.model_card.stream_logs", return_value=["l1", "l2"]
    ) as fn:
        yield fn


@pytest.fixture
def port_is_free():
    """Patch the port picker's unmanaged-collision probe to 'free'."""
    with patch(
        "llauncher.ui.components.port_picker.is_port_in_use", return_value=False
    ) as fn:
        yield fn


# ---------------------------------------------------------------------------
# start — delegation gate × both doors (local node)
# ---------------------------------------------------------------------------
class TestStartDispatch:
    """Starting a stopped local model dispatches through exactly one door."""

    def test_start_with_no_agent_dispatches_in_process_ops_start(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        mock_ops.start.assert_called_once_with(MODEL, PORT, caller="ui")
        mock_local_agent_node.start_server.assert_not_called()

    def test_start_with_agent_present_delegates_over_http(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free, forbid_direct_http,
    ):
        mock_should_delegate.return_value = True

        with forbid_direct_http():
            at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
            _set_port(at)
            _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        mock_local_agent_node.start_server.assert_called_once_with(MODEL, PORT)
        mock_ops.start.assert_not_called()

    def test_start_button_without_a_port_is_disabled_and_dispatches_nothing(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node, mock_occupancy,
    ):
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)

        assert at.button(key=f"toggle_start_local_{MODEL}").disabled
        # Defence-in-depth (ADR-010): even a click that somehow fires must
        # not reach either dispatch door while the picker yields no port.
        _click_and_run(at, f"toggle_start_local_{MODEL}")
        assert not at.exception
        mock_ops.start.assert_not_called()
        mock_local_agent_node.start_server.assert_not_called()

    def test_start_with_unknown_config_is_disabled_and_dispatches_nothing(
        self, tab_harness, mock_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
    ):
        # mock_state.models is empty — the card has no config to start.
        at = _card(tab_harness, mock_state, mock_aggregator, "local", model_dict)

        assert not at.exception
        assert at.button(key=f"toggle_start_local_{MODEL}").disabled
        mock_ops.start.assert_not_called()
        mock_local_agent_node.start_server.assert_not_called()

    def test_start_blocked_by_can_start_gate_dispatches_nothing(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        # Pinned as-shipped (#333): the UI pre-validates via state.can_start
        # and, on rejection, sends *no* verb anywhere.
        card_state.can_start.return_value = (False, "model path missing")

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        card_state.can_start.assert_called_once_with(
            card_state.models[MODEL], caller="ui", port=PORT
        )
        mock_ops.start.assert_not_called()
        mock_local_agent_node.start_server.assert_not_called()

    def test_start_when_config_vanished_between_render_and_click_dispatches_nothing(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        del card_state.models[MODEL]  # config removed by a concurrent actor
        # The click run re-renders first, so the render-time config guard
        # (disabled button, no picker) absorbs this before `_handle_start`'s
        # own defence-in-depth re-check can fire — either guard's job is the
        # same: no verb leaves the card for a config that no longer exists.
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        mock_ops.start.assert_not_called()
        mock_local_agent_node.start_server.assert_not_called()

    def test_delegated_start_null_agent_body_is_handled_not_raised(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        # RemoteNode.start_server is dict | None; a 200-with-null body must
        # land in the error branch via the card's `or {}` seam, not raise.
        mock_should_delegate.return_value = True
        mock_local_agent_node.start_server.return_value = None

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        mock_local_agent_node.start_server.assert_called_once_with(MODEL, PORT)

    def test_rejected_in_process_start_stays_a_single_dispatch(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        from tests.ui.conftest import make_op_result

        mock_ops.start.return_value = make_op_result(
            success=False, action="rejected_occupied", message="port occupied"
        )

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        mock_ops.start.assert_called_once_with(MODEL, PORT, caller="ui")


# ---------------------------------------------------------------------------
# start failure — the error must survive _handle_start's trailing st.rerun()
# (#401): a failure message written to session_state on one run must still
# be readable (via at.error) on the *next* run, not just the run it failed on.
# ---------------------------------------------------------------------------
class TestStickyStartError:
    """A start failure's message survives the handler's own ``st.rerun()``."""

    def test_rejected_in_process_start_leaves_a_sticky_error_after_rerun(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        from tests.ui.conftest import make_op_result

        mock_ops.start.return_value = make_op_result(
            success=False, action="rejected_occupied", message="port occupied"
        )

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        # The handler's st.rerun() already folded into _click_and_run's
        # at.run(); a bare st.error() call would have been wiped by that
        # rerun. Assert the message is still present on the AppTest's
        # element tree *after* the rerun completed.
        assert not at.exception
        assert any("port occupied" in e.value for e in at.error)
        assert at.session_state[f"start_error_local_{MODEL}"] == "port occupied"

    def test_can_start_gate_rejection_leaves_a_sticky_error_after_rerun(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        card_state.can_start.return_value = (False, "model path missing")

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        assert any("model path missing" in e.value for e in at.error)

    def test_delegated_start_failure_leaves_a_sticky_error_after_rerun(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        mock_should_delegate.return_value = True
        mock_local_agent_node.start_server.return_value = {
            "success": False,
            "error": "agent refused",
        }

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        assert any("agent refused" in e.value for e in at.error)

    def test_dismiss_clears_the_sticky_error(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        from tests.ui.conftest import make_op_result

        mock_ops.start.return_value = make_op_result(
            success=False, action="rejected_occupied", message="port occupied"
        )

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")
        assert any("port occupied" in e.value for e in at.error)

        _click_and_run(at, f"start_error_local_{MODEL}_dismiss")

        assert not at.exception
        assert not at.error
        assert at.session_state[f"start_error_local_{MODEL}"] is None

    def test_a_successful_start_clears_a_previously_sticky_error(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        from tests.ui.conftest import make_op_result

        # First attempt fails and arms the sticky error.
        mock_ops.start.return_value = make_op_result(
            success=False, action="rejected_occupied", message="port occupied"
        )
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")
        assert any("port occupied" in e.value for e in at.error)

        # A later successful attempt (e.g. port freed up) must not leave the
        # stale failure message behind.
        mock_ops.start.return_value = make_op_result(
            success=True, action="started", message="started"
        )
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        assert not at.error
        assert at.session_state[f"start_error_local_{MODEL}"] is None

    def test_no_prior_failure_renders_no_error_and_no_dismiss_button(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)

        assert not at.exception
        assert not at.error
        keys = {b.key for b in at.button}
        assert f"start_error_local_{MODEL}_dismiss" not in keys


# ---------------------------------------------------------------------------
# stop — delegation gate × both doors (local node)
# ---------------------------------------------------------------------------
class TestStopDispatch:
    """Stopping a running local model dispatches through exactly one door."""

    def test_stop_with_no_agent_dispatches_ops_stop_not_legacy_state_path(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node, mock_stream_logs,
    ):
        at = _card(
            tab_harness, card_state, mock_aggregator, "local", model_dict, _server()
        )
        _click_and_run(at, f"toggle_stop_local_{MODEL}")

        assert not at.exception
        # Current main shape (#332 / PR #344): stop routes through ops like
        # its siblings — lockfile removal + durable audit at parity with
        # CLI/MCP. The legacy LauncherState.stop_server path stays dead.
        mock_ops.stop.assert_called_once_with(PORT, caller="ui")
        card_state.stop_server.assert_not_called()
        mock_local_agent_node.stop_server.assert_not_called()

    def test_stop_with_agent_present_delegates_over_http(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node, mock_stream_logs,
        forbid_direct_http,
    ):
        mock_should_delegate.return_value = True

        with forbid_direct_http():
            at = _card(
                tab_harness, card_state, mock_aggregator, "local",
                model_dict, _server(),
            )
            _click_and_run(at, f"toggle_stop_local_{MODEL}")

        assert not at.exception
        mock_local_agent_node.stop_server.assert_called_once_with(PORT)
        mock_ops.stop.assert_not_called()
        card_state.stop_server.assert_not_called()

    def test_delegated_stop_null_agent_body_is_handled_not_raised(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node, mock_stream_logs,
    ):
        mock_should_delegate.return_value = True
        mock_local_agent_node.stop_server.return_value = None

        at = _card(
            tab_harness, card_state, mock_aggregator, "local", model_dict, _server()
        )
        _click_and_run(at, f"toggle_stop_local_{MODEL}")

        assert not at.exception
        mock_local_agent_node.stop_server.assert_called_once_with(PORT)


# ---------------------------------------------------------------------------
# start/stop on a remote node — the aggregator facade is the only door
# ---------------------------------------------------------------------------
class TestRemoteNodeDispatch:
    """Remote-node cards dispatch through RemoteAggregator, never ops/agent."""

    def test_start_on_remote_node_routes_through_aggregator(
        self, tab_harness, mock_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node, port_is_free,
        forbid_direct_http,
    ):
        mock_aggregator.start_on_node.return_value = {"success": True}

        with forbid_direct_http():
            at = _card(tab_harness, mock_state, mock_aggregator, "gpu-rig", model_dict)
            _set_port(at, node_name="gpu-rig")
            _click_and_run(at, f"toggle_start_gpu-rig_{MODEL}")

        assert not at.exception
        mock_aggregator.start_on_node.assert_called_once_with("gpu-rig", MODEL, PORT)
        mock_ops.start.assert_not_called()
        mock_local_agent_node.start_server.assert_not_called()

    def test_start_on_remote_node_failure_envelope_stays_a_single_dispatch(
        self, tab_harness, mock_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node, port_is_free,
    ):
        mock_aggregator.start_on_node.return_value = {"success": False, "error": "boom"}

        at = _card(tab_harness, mock_state, mock_aggregator, "gpu-rig", model_dict)
        _set_port(at, node_name="gpu-rig")
        _click_and_run(at, f"toggle_start_gpu-rig_{MODEL}")

        assert not at.exception
        mock_aggregator.start_on_node.assert_called_once_with("gpu-rig", MODEL, PORT)

    def test_start_on_remote_node_without_aggregator_dispatches_nothing(
        self, tab_harness, mock_state, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node, port_is_free,
    ):
        at = _card(tab_harness, mock_state, None, "gpu-rig", model_dict)
        _set_port(at, node_name="gpu-rig")
        _click_and_run(at, f"toggle_start_gpu-rig_{MODEL}")

        assert not at.exception
        mock_ops.start.assert_not_called()
        mock_local_agent_node.start_server.assert_not_called()

    @pytest.mark.parametrize(
        "aggregator_result",
        [
            {"success": True, "message": "stopped"},
            {"success": False, "message": "node error"},
            None,  # unreachable / null body
            "unexpected plain string",  # defensive _parse_aggregator_result arm
        ],
        ids=["success-dict", "failure-dict", "none", "string"],
    )
    def test_stop_on_remote_node_routes_through_aggregator(
        self, tab_harness, mock_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node, aggregator_result,
    ):
        mock_aggregator.stop_on_node.return_value = aggregator_result
        mock_aggregator.get_logs_on_node.return_value = None

        at = _card(
            tab_harness, mock_state, mock_aggregator, "gpu-rig",
            model_dict, _server(node_name="gpu-rig"),
        )
        _click_and_run(at, f"toggle_stop_gpu-rig_{MODEL}")

        assert not at.exception
        mock_aggregator.stop_on_node.assert_called_once_with("gpu-rig", PORT)
        mock_ops.stop.assert_not_called()
        mock_local_agent_node.stop_server.assert_not_called()

    def test_stop_on_remote_node_without_aggregator_dispatches_nothing(
        self, tab_harness, mock_state, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
    ):
        at = _card(
            tab_harness, mock_state, None, "gpu-rig",
            model_dict, _server(node_name="gpu-rig"),
        )
        _click_and_run(at, f"toggle_stop_gpu-rig_{MODEL}")

        assert not at.exception
        mock_ops.stop.assert_not_called()
        mock_local_agent_node.stop_server.assert_not_called()


# ---------------------------------------------------------------------------
# swap — reachable only via the eviction confirm gate (#330 matrix)
# ---------------------------------------------------------------------------
class TestEvictionConfirmGate:
    """Starting into an occupied port must offer eviction, and only an
    explicit Confirm dispatches the swap verb.

    The dialog is render-transient (it lives inside the start button's click
    branch, not behind a session-state flag like delete-confirm), so the
    confirm/cancel dispatch seams are driven on ``_render_eviction_dialog``
    directly — the function that owns the delegation-gated swap dispatch —
    while the reroute itself is driven through the full card.
    """

    def test_start_into_occupied_port_offers_eviction_instead_of_dispatching(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        card_state.running[PORT] = MagicMock(config_name="occupant-model")
        mock_occupancy.running[PORT] = MagicMock(config_name="occupant-model")

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        # No verb fired anywhere — the click was rerouted to the confirm gate.
        mock_ops.start.assert_not_called()
        mock_ops.swap.assert_not_called()
        mock_local_agent_node.start_server.assert_not_called()
        mock_local_agent_node.swap_server.assert_not_called()
        # The gate's two affordances are now live.
        assert at.button(key=f"evict_confirm_local_{PORT}_{MODEL}") is not None
        assert at.button(key=f"evict_cancel_local_{PORT}_{MODEL}") is not None

    def test_eviction_dialog_survives_an_unrelated_rerun_then_confirms_once(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node,
        mock_occupancy, port_is_free,
    ):
        """Regression for #412: the dialog used to be render-transient — it
        lived only inside ``_handle_start``'s click branch, with no
        ``session_state`` flag recording that a confirmation was pending. A
        rerun from *anywhere else* on the card (e.g. the unrelated "Refresh
        logs" button on a sibling card, simulated here via a bare
        ``at.run()``) would silently drop the dialog before the operator
        could click Confirm, dead-ending the flow. The fix arms a
        ``(node, port, model)``-keyed flag on entry and gates the dialog's
        render on it, so it survives any number of reruns until an explicit
        Cancel or Confirm.
        """
        card_state.running[PORT] = MagicMock(config_name="occupant-model")
        mock_occupancy.running[PORT] = MagicMock(config_name="occupant-model")

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert at.session_state[_eviction_flag_key("local", PORT, MODEL)] is True
        assert at.button(key=f"evict_confirm_local_{PORT}_{MODEL}") is not None

        # An unrelated rerun (no button click) must not erase the pending
        # confirmation — this is exactly what the old render-transient
        # dialog got wrong.
        at.run()

        assert not at.exception
        assert at.session_state[_eviction_flag_key("local", PORT, MODEL)] is True
        assert at.button(key=f"evict_confirm_local_{PORT}_{MODEL}") is not None
        assert at.button(key=f"evict_cancel_local_{PORT}_{MODEL}") is not None
        mock_ops.swap.assert_not_called()

        _click_and_run(at, f"evict_confirm_local_{PORT}_{MODEL}")

        assert not at.exception
        mock_ops.swap.assert_called_once_with(MODEL, PORT, caller="ui")
        assert at.session_state[_eviction_flag_key("local", PORT, MODEL)] is False

    def test_eviction_confirm_with_no_agent_dispatches_ops_swap(
        self, tab_harness, card_state, mock_ops, mock_should_delegate,
        mock_local_agent_node,
    ):
        at = tab_harness(
            _render_eviction_dialog, card_state, "local", PORT, MODEL,
            _eviction_flag_key("local", PORT, MODEL),
        )
        _click_and_run(at, f"evict_confirm_local_{PORT}_{MODEL}")

        assert not at.exception
        mock_ops.swap.assert_called_once_with(MODEL, PORT, caller="ui")
        mock_local_agent_node.swap_server.assert_not_called()

    def test_eviction_confirm_with_agent_present_delegates_swap_over_http(
        self, tab_harness, card_state, mock_ops, mock_should_delegate,
        mock_local_agent_node, forbid_direct_http,
    ):
        mock_should_delegate.return_value = True

        with forbid_direct_http():
            at = tab_harness(
                _render_eviction_dialog, card_state, "local", PORT, MODEL,
            _eviction_flag_key("local", PORT, MODEL),
            )
            _click_and_run(at, f"evict_confirm_local_{PORT}_{MODEL}")

        assert not at.exception
        mock_local_agent_node.swap_server.assert_called_once_with(MODEL, PORT)
        mock_ops.swap.assert_not_called()

    def test_eviction_cancel_dispatches_nothing(
        self, tab_harness, card_state, mock_ops, mock_should_delegate,
        mock_local_agent_node,
    ):
        at = tab_harness(
            _render_eviction_dialog, card_state, "local", PORT, MODEL,
            _eviction_flag_key("local", PORT, MODEL),
        )
        _click_and_run(at, f"evict_cancel_local_{PORT}_{MODEL}")

        assert not at.exception
        mock_ops.swap.assert_not_called()
        mock_local_agent_node.swap_server.assert_not_called()

    @pytest.mark.parametrize(
        ("success", "action", "previous_model"),
        [
            (True, "swapped", "occupant-model"),
            (True, "already_running", None),
            (False, "rolled_back", "occupant-model"),
            (False, "failed", "occupant-model"),
            (False, "rejected_stop_failed", "occupant-model"),
            (False, "rejected_empty", None),
        ],
        ids=lambda v: str(v),
    )
    def test_every_swap_result_envelope_routes_through_the_single_ops_swap_dispatch(
        self, tab_harness, card_state, mock_ops, mock_should_delegate,
        mock_local_agent_node, success, action, previous_model,
    ):
        from tests.ui.conftest import make_op_result

        mock_ops.swap.return_value = make_op_result(
            success=success, action=action, message="m", previous_model=previous_model
        )

        at = tab_harness(
            _render_eviction_dialog, card_state, "local", PORT, MODEL,
            _eviction_flag_key("local", PORT, MODEL),
        )
        _click_and_run(at, f"evict_confirm_local_{PORT}_{MODEL}")

        assert not at.exception
        mock_ops.swap.assert_called_once_with(MODEL, PORT, caller="ui")

    def test_delegated_eviction_null_agent_body_is_handled_not_raised(
        self, tab_harness, card_state, mock_ops, mock_should_delegate,
        mock_local_agent_node,
    ):
        mock_should_delegate.return_value = True
        mock_local_agent_node.swap_server.return_value = None

        at = tab_harness(
            _render_eviction_dialog, card_state, "local", PORT, MODEL,
            _eviction_flag_key("local", PORT, MODEL),
        )
        _click_and_run(at, f"evict_confirm_local_{PORT}_{MODEL}")

        assert not at.exception
        mock_local_agent_node.swap_server.assert_called_once_with(MODEL, PORT)


# ---------------------------------------------------------------------------
# delete — two-step confirm gate onto the ungated ops.delete_model
# ---------------------------------------------------------------------------
class TestDeleteConfirmGate:
    """Delete requires an explicit second click; only Confirm dispatches."""

    def test_first_delete_click_only_arms_the_gate(
        self, tab_harness, card_state, mock_aggregator, model_dict, mock_ops,
    ):
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _click_and_run(at, f"delete_local_{MODEL}_enabled")

        assert not at.exception
        mock_ops.delete_model.assert_not_called()
        assert at.session_state[f"deleting_local_{MODEL}"] is True
        assert at.button(key=f"delete_confirm_local_{MODEL}") is not None

    def test_delete_confirm_dispatches_ops_delete_model_with_ui_caller(
        self, tab_harness, card_state, mock_aggregator, model_dict, mock_ops,
    ):
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _click_and_run(at, f"delete_local_{MODEL}_enabled")
        _click_and_run(at, f"delete_confirm_local_{MODEL}")

        assert not at.exception
        mock_ops.delete_model.assert_called_once_with(MODEL, caller="ui")
        assert at.session_state[f"deleting_local_{MODEL}"] is False

    def test_delete_cancel_disarms_the_gate_without_dispatching(
        self, tab_harness, card_state, mock_aggregator, model_dict, mock_ops,
    ):
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _click_and_run(at, f"delete_local_{MODEL}_enabled")
        _click_and_run(at, f"delete_cancel_local_{MODEL}")

        assert not at.exception
        mock_ops.delete_model.assert_not_called()
        assert at.session_state[f"deleting_local_{MODEL}"] is False

    @pytest.mark.parametrize(
        ("action", "message"),
        [
            ("rejected_in_use", "model is running"),
            ("not_found", "no such model"),
        ],
    )
    def test_rejected_delete_stays_a_single_dispatch_and_disarms(
        self, tab_harness, card_state, mock_aggregator, model_dict, mock_ops,
        action, message,
    ):
        from tests.ui.conftest import make_op_result

        mock_ops.delete_model.return_value = make_op_result(
            success=False, action=action, message=message
        )

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _click_and_run(at, f"delete_local_{MODEL}_enabled")
        _click_and_run(at, f"delete_confirm_local_{MODEL}")

        assert not at.exception
        mock_ops.delete_model.assert_called_once_with(MODEL, caller="ui")
        assert at.session_state[f"deleting_local_{MODEL}"] is False


# ---------------------------------------------------------------------------
# edit/delete gating — local vs remote, stopped vs running
# ---------------------------------------------------------------------------
class TestEditDeleteGating:
    """Edit/Delete are live only for stopped local models."""

    def test_edit_click_arms_the_forms_editing_flag(
        self, tab_harness, card_state, mock_aggregator, model_dict, mock_ops,
    ):
        # The card's edit dispatch is the session-state handoff the forms
        # tab (`render_edit_model`, SP-4) consumes — no engine call fires.
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _click_and_run(at, f"edit_local_{MODEL}_enabled")

        assert not at.exception
        assert at.session_state[f"editing_{MODEL}"] is True

    def test_remote_model_edit_and_delete_are_disabled(
        self, tab_harness, mock_state, mock_aggregator, model_dict, mock_ops,
    ):
        at = _card(tab_harness, mock_state, mock_aggregator, "gpu-rig", model_dict)

        assert not at.exception
        assert at.button(key=f"edit_gpu-rig_{MODEL}_disabled").disabled
        assert at.button(key=f"delete_gpu-rig_{MODEL}_disabled").disabled
        mock_ops.delete_model.assert_not_called()

    def test_running_model_offers_no_edit_or_delete_controls(
        self, tab_harness, card_state, mock_aggregator, model_dict, mock_ops,
        mock_stream_logs,
    ):
        at = _card(
            tab_harness, card_state, mock_aggregator, "local", model_dict, _server()
        )

        assert not at.exception
        keys = {b.key for b in at.button}
        assert f"edit_local_{MODEL}_enabled" not in keys
        assert f"delete_local_{MODEL}_enabled" not in keys


# ---------------------------------------------------------------------------
# logs expander — read seams + the refresh rerun
# ---------------------------------------------------------------------------
class TestLogsExpander:
    """Log reads go through core (local) / the aggregator (remote), only
    while the server runs."""

    def test_local_logs_are_read_through_core_stream_logs(
        self, tab_harness, card_state, mock_aggregator, model_dict, mock_stream_logs,
    ):
        at = _card(
            tab_harness, card_state, mock_aggregator, "local", model_dict, _server()
        )

        assert not at.exception
        mock_stream_logs.assert_called_once_with(pid=PID, lines=100)
        mock_aggregator.get_logs_on_node.assert_not_called()

    def test_refresh_click_reruns_and_rereads_the_logs(
        self, tab_harness, card_state, mock_aggregator, model_dict, mock_stream_logs,
    ):
        at = _card(
            tab_harness, card_state, mock_aggregator, "local", model_dict, _server()
        )
        reads_before = mock_stream_logs.call_count

        _click_and_run(at, f"refresh_logs_local_{MODEL}")

        assert not at.exception
        assert mock_stream_logs.call_count > reads_before

    def test_remote_logs_are_read_through_the_aggregator(
        self, tab_harness, mock_state, mock_aggregator, model_dict, mock_stream_logs,
        forbid_direct_http,
    ):
        mock_aggregator.get_logs_on_node.return_value = ["remote line"]

        with forbid_direct_http():
            at = _card(
                tab_harness, mock_state, mock_aggregator, "gpu-rig",
                model_dict, _server(node_name="gpu-rig"),
            )

        assert not at.exception
        mock_aggregator.get_logs_on_node.assert_called_once_with("gpu-rig", PORT, 100)
        mock_stream_logs.assert_not_called()

    def test_stopped_model_reads_no_logs(
        self, tab_harness, card_state, mock_aggregator, model_dict, mock_stream_logs,
    ):
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)

        assert not at.exception
        mock_stream_logs.assert_not_called()
        mock_aggregator.get_logs_on_node.assert_not_called()


# ---------------------------------------------------------------------------
# port picker branches, driven through the card
# ---------------------------------------------------------------------------
class TestPortPickerThroughCard:
    """The ADR-010 picker gates the start verb per its four inline states."""

    def test_blacklisted_port_disables_start_and_dispatches_nothing(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node, mock_occupancy,
    ):
        with patch("llauncher.ui.components.port_picker.BLACKLISTED_PORTS", [PORT]):
            at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
            _set_port(at)
            at.run()

            assert at.button(key=f"toggle_start_local_{MODEL}").disabled
            _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        mock_ops.start.assert_not_called()
        mock_local_agent_node.start_server.assert_not_called()

    def test_managed_collision_keeps_start_enabled_for_the_eviction_handoff(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_occupancy, port_is_free,
    ):
        card_state.running[PORT] = MagicMock(config_name="occupant-model")

        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        at.run()

        assert not at.exception
        assert not at.button(key=f"toggle_start_local_{MODEL}").disabled
        mock_ops.start.assert_not_called()  # rendering alone dispatches nothing

    def test_unmanaged_collision_still_dispatches_the_verb_for_backend_rejection(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_should_delegate, mock_local_agent_node, mock_occupancy,
    ):
        # The picker warns but returns the port: occupancy rejection is the
        # ops layer's call (`rejected_occupied`), not the UI's.
        with patch(
            "llauncher.ui.components.port_picker.is_port_in_use", return_value=True
        ):
            at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
            _set_port(at)
            _click_and_run(at, f"toggle_start_local_{MODEL}")

        assert not at.exception
        mock_ops.start.assert_called_once_with(MODEL, PORT, caller="ui")

    def test_free_port_enables_start(
        self, tab_harness, card_state, mock_aggregator, model_dict,
        mock_ops, mock_occupancy, port_is_free,
    ):
        at = _card(tab_harness, card_state, mock_aggregator, "local", model_dict)
        _set_port(at)
        at.run()

        assert not at.exception
        assert not at.button(key=f"toggle_start_local_{MODEL}").disabled
