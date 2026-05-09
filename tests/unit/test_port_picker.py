"""Tests for the port picker component (M4 Slice 13 stage 2 / #50)."""

from unittest.mock import MagicMock, patch


class TestRenderPortPicker:
    """Behavioural tests for ``render_port_picker``."""

    def test_returns_none_when_input_is_none(self):
        """User hasn't typed anything yet → no port, no caption."""
        from llauncher.ui.components.port_picker import render_port_picker

        state = MagicMock()
        state.running = {}

        with patch("llauncher.ui.components.port_picker.st") as mock_st, patch(
            "llauncher.ui.components.port_picker.is_port_in_use",
            return_value=False,
        ):
            mock_st.number_input.return_value = None

            result = render_port_picker(state, key_prefix="t1")

            assert result is None
            mock_st.error.assert_not_called()
            mock_st.warning.assert_not_called()

    def test_blacklisted_port_renders_error_and_returns_none(self):
        from llauncher.ui.components.port_picker import render_port_picker

        state = MagicMock()
        state.running = {}

        with patch("llauncher.ui.components.port_picker.st") as mock_st, patch(
            "llauncher.ui.components.port_picker.BLACKLISTED_PORTS", [9999]
        ), patch(
            "llauncher.ui.components.port_picker.is_port_in_use",
            return_value=False,
        ):
            mock_st.number_input.return_value = 9999

            result = render_port_picker(state, key_prefix="t2")

            assert result is None
            mock_st.error.assert_called_once()
            error_text = mock_st.error.call_args[0][0]
            assert "9999" in error_text
            assert "blacklisted" in error_text.lower()

    def test_in_use_by_managed_peer_renders_warning_returns_port(self):
        from llauncher.ui.components.port_picker import render_port_picker

        existing = MagicMock()
        existing.config_name = "other_model"
        state = MagicMock()
        state.running = {8080: existing}

        with patch("llauncher.ui.components.port_picker.st") as mock_st, patch(
            "llauncher.ui.components.port_picker.BLACKLISTED_PORTS", []
        ), patch(
            "llauncher.ui.components.port_picker.is_port_in_use",
            return_value=False,
        ):
            mock_st.number_input.return_value = 8080

            result = render_port_picker(state, key_prefix="t3", model_name="my_model")

            assert result == 8080
            mock_st.warning.assert_called_once()
            warn_text = mock_st.warning.call_args[0][0]
            assert "other_model" in warn_text
            assert "eviction" in warn_text.lower()

    def test_in_use_by_unmanaged_process_warns_and_returns_port(self):
        from llauncher.ui.components.port_picker import render_port_picker

        state = MagicMock()
        state.running = {}  # nothing managed at this port

        with patch("llauncher.ui.components.port_picker.st") as mock_st, patch(
            "llauncher.ui.components.port_picker.BLACKLISTED_PORTS", []
        ), patch(
            "llauncher.ui.components.port_picker.is_port_in_use",
            return_value=True,  # something else owns it
        ):
            mock_st.number_input.return_value = 5050

            result = render_port_picker(state, key_prefix="t4")

            assert result == 5050
            mock_st.warning.assert_called_once()
            warn_text = mock_st.warning.call_args[0][0]
            assert "rejected_occupied" in warn_text

    def test_valid_free_port_returns_port_with_no_caption(self):
        from llauncher.ui.components.port_picker import render_port_picker

        state = MagicMock()
        state.running = {}

        with patch("llauncher.ui.components.port_picker.st") as mock_st, patch(
            "llauncher.ui.components.port_picker.BLACKLISTED_PORTS", []
        ), patch(
            "llauncher.ui.components.port_picker.is_port_in_use",
            return_value=False,
        ):
            mock_st.number_input.return_value = 7777

            result = render_port_picker(state, key_prefix="t5")

            assert result == 7777
            mock_st.error.assert_not_called()
            mock_st.warning.assert_not_called()

    def test_picker_does_not_call_find_available_port(self):
        """ADR-010 invariant: the picker is pure UI, no auto-allocation."""
        from llauncher.ui.components import port_picker

        state = MagicMock()
        state.running = {}

        with patch("llauncher.ui.components.port_picker.st") as mock_st, patch(
            "llauncher.core.process.find_available_port"
        ) as mock_find:
            mock_st.number_input.return_value = 7777

            port_picker.render_port_picker(state, key_prefix="t6")

            mock_find.assert_not_called()

    def test_picker_does_not_call_state_start_server(self):
        """The picker must not invoke any verb."""
        from llauncher.ui.components.port_picker import render_port_picker

        state = MagicMock()
        state.running = {}

        with patch("llauncher.ui.components.port_picker.st") as mock_st, patch(
            "llauncher.ui.components.port_picker.is_port_in_use",
            return_value=False,
        ), patch(
            "llauncher.ui.components.port_picker.BLACKLISTED_PORTS", []
        ):
            mock_st.number_input.return_value = 8001

            render_port_picker(state, key_prefix="t7")

            state.start_server.assert_not_called()

    def test_picker_ignores_self_collision_when_model_name_matches(self):
        """A port held by the *same* model is not a collision."""
        from llauncher.ui.components.port_picker import render_port_picker

        existing = MagicMock()
        existing.config_name = "my_model"
        state = MagicMock()
        state.running = {8080: existing}

        with patch("llauncher.ui.components.port_picker.st") as mock_st, patch(
            "llauncher.ui.components.port_picker.BLACKLISTED_PORTS", []
        ), patch(
            "llauncher.ui.components.port_picker.is_port_in_use",
            return_value=False,
        ):
            mock_st.number_input.return_value = 8080

            result = render_port_picker(state, key_prefix="t8", model_name="my_model")

            assert result == 8080
            mock_st.warning.assert_not_called()
