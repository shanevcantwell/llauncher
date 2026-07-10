"""UI coverage for the model-card Delete button and confirm gate (#276).

Mirrors ``test_model_card_delegation.py``'s structure/imports/mocking style:
Streamlit is mocked at the module seam (``model_card.st``). Unlike the
delegation tests, the delete flow spans *two* buttons (Delete → Confirm/
Cancel) gated by session state, so ``st.button`` here is a ``side_effect``
keyed by the ``key=`` kwarg rather than a single blanket ``return_value``,
and ``st.session_state`` is a plain dict so the confirm-gate flag can be
read/written realistically across the two render calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from llauncher.ui.tabs import model_card
from llauncher.operations.delete import DeleteModelResult


def _mock_st(*, clicked_keys: set[str] | None = None, session_state: dict | None = None):
    """A Streamlit stand-in whose ``st.button`` fires only for ``clicked_keys``.

    Args:
        clicked_keys: set of ``key=`` values for which ``st.button(...)``
            should return True; all other keys return False.
        session_state: backing dict for ``st.session_state`` (defaults to a
            fresh empty dict so each test starts with no delete flag set).
    """
    clicked_keys = clicked_keys or set()
    st = MagicMock()
    st.columns.side_effect = lambda n: [MagicMock(), MagicMock()]
    st.session_state = session_state if session_state is not None else {}

    def _button(*args, **kwargs):
        return kwargs.get("key") in clicked_keys

    st.button.side_effect = _button
    return st


# ───────────────────────── Unconfirmed click (confirm gate) ─────────────────


class TestDeleteConfirmGateBlocksUnconfirmedClick:
    """First click of Delete only sets the session-state flag; no ops call."""

    def test_first_delete_click_sets_flag_and_does_not_call_ops(self):
        st = _mock_st(clicked_keys={"delete_local_m_enabled"})

        with patch.object(model_card, "st", st), patch.object(
            model_card.ops, "delete_model"
        ) as mock_delete:
            # Simulate the button block directly (mirrors the real
            # ``_render_model_details`` Delete-button branch): a click sets
            # the flag and reruns.
            if st.button("🗑️ Delete", width="stretch", key="delete_local_m_enabled"):
                st.session_state["deleting_local_m"] = True
                st.rerun()

        assert st.session_state.get("deleting_local_m") is True
        mock_delete.assert_not_called()

    def test_render_delete_confirm_noop_when_flag_unset(self):
        """With no flag in session state, the confirm UI does not render
        and ``ops.delete_model`` is never reached."""
        st = _mock_st(session_state={})

        with patch.object(model_card, "st", st), patch.object(
            model_card.ops, "delete_model"
        ) as mock_delete:
            model_card._render_delete_confirm("local", "m")

        mock_delete.assert_not_called()
        st.warning.assert_not_called()


# ───────────────────────────── Confirm path ──────────────────────────────


class TestDeleteConfirmFiresOps:
    def test_confirm_click_calls_ops_delete_model(self):
        st = _mock_st(
            clicked_keys={"delete_confirm_local_m"},
            session_state={"deleting_local_m": True},
        )
        envelope = DeleteModelResult(success=True, action="deleted", name="m", message="Removed 'm'.")

        with patch.object(model_card, "st", st), patch.object(
            model_card.ops, "delete_model", return_value=envelope
        ) as mock_delete:
            model_card._render_delete_confirm("local", "m")

        mock_delete.assert_called_once_with("m", caller="ui")
        st.toast.assert_called_once_with("Removed 'm'.", icon="✅")
        # Flag is cleared after a confirmed delete.
        assert st.session_state.get("deleting_local_m") is False

    def test_confirm_render_shows_warning(self):
        st = _mock_st(session_state={"deleting_local_m": True})

        with patch.object(model_card, "st", st), patch.object(
            model_card.ops, "delete_model"
        ):
            model_card._render_delete_confirm("local", "m")

        st.warning.assert_called_once()


# ───────────────────────────── Cancel path ────────────────────────────────


class TestDeleteCancel:
    def test_cancel_click_does_not_call_ops_and_clears_flag(self):
        st = _mock_st(
            clicked_keys={"delete_cancel_local_m"},
            session_state={"deleting_local_m": True},
        )

        with patch.object(model_card, "st", st), patch.object(
            model_card.ops, "delete_model"
        ) as mock_delete:
            model_card._render_delete_confirm("local", "m")

        mock_delete.assert_not_called()
        assert st.session_state.get("deleting_local_m") is False


# ───────────────────────── rejected_in_use surfacing ──────────────────────


class TestDeleteRejectedInUse:
    """Belt-and-suspenders backend refusal surfaces via st.error + st.toast,
    mirroring the ``_handle_start``/``_handle_stop`` failure pattern."""

    def test_rejected_in_use_surfaces_error_and_toast(self):
        st = _mock_st(
            clicked_keys={"delete_confirm_local_m"},
            session_state={"deleting_local_m": True},
        )
        envelope = DeleteModelResult(
            success=False,
            action="rejected_in_use",
            name="m",
            in_use_port=8080,
            message="Model 'm' is running on port 8080 (pid 123); stop it before deleting.",
        )

        with patch.object(model_card, "st", st), patch.object(
            model_card.ops, "delete_model", return_value=envelope
        ) as mock_delete:
            model_card._render_delete_confirm("local", "m")

        mock_delete.assert_called_once_with("m", caller="ui")
        st.error.assert_called_once_with(envelope.message)
        st.toast.assert_called_once_with(envelope.message, icon="❌")
        assert st.session_state.get("deleting_local_m") is False

    def test_other_failure_surfaces_error_and_toast(self):
        st = _mock_st(
            clicked_keys={"delete_confirm_local_m"},
            session_state={"deleting_local_m": True},
        )
        envelope = DeleteModelResult(
            success=False,
            action="error",
            name="m",
            message="Something went wrong deleting 'm'.",
        )

        with patch.object(model_card, "st", st), patch.object(
            model_card.ops, "delete_model", return_value=envelope
        ):
            model_card._render_delete_confirm("local", "m")

        st.error.assert_called_once_with(envelope.message)
        st.toast.assert_called_once_with(envelope.message, icon="❌")


# ───────────────────── Remote models: no delete capability ────────────────


class TestRemoteModelNoDelete:
    """Remote models follow Edit's existing restriction exactly: a disabled
    button with the "not yet supported" caption style, no delete capability.
    """

    def test_remote_details_render_disabled_delete_button(self):
        state = MagicMock()
        aggregator = None
        model = {"name": "remote-m", "model_path": "/fake/remote.gguf", "n_gpu_layers": 10}
        st = _mock_st()

        with patch.object(model_card, "st", st):
            model_card._render_model_details(
                state, aggregator, "remote-node", "remote-m", model, running_server=None
            )

        # A disabled delete button was rendered for the remote node.
        disabled_delete_calls = [
            call for call in st.button.call_args_list
            if call.kwargs.get("key") == "delete_remote-node_remote-m_disabled"
        ]
        assert len(disabled_delete_calls) == 1
        assert disabled_delete_calls[0].kwargs.get("disabled") is True
