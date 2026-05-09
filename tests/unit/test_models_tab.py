"""Tests for the consolidated Models tab (M4 Slice 13 stage 2 / #50)."""

from unittest.mock import MagicMock, patch


class TestRenderModelsTab:
    """Composition-root tests for ``render_models_tab``."""

    def _mk_state(self, models=None, running=None):
        state = MagicMock()
        state.models = models or {}
        state.running = running or {}
        return state

    def _patch_st(self, mock_st):
        """Wire up the common Streamlit mocks the tab needs."""
        expander = MagicMock()
        expander.__enter__ = MagicMock(return_value=None)
        expander.__exit__ = MagicMock(return_value=None)
        mock_st.expander.return_value = expander

        def mock_columns(n):
            count = len(n) if isinstance(n, list) else n
            return [MagicMock() for _ in range(count)]
        mock_st.columns.side_effect = mock_columns
        mock_st.session_state.get.return_value = False
        mock_st.button.return_value = False
        return mock_st

    def test_renders_registry_then_add_form_then_cards(self):
        from llauncher.ui.tabs.models import render_models_tab

        state = self._mk_state(models={
            "m1": MagicMock(to_dict=MagicMock(return_value={"name": "m1"})),
        })

        with patch("llauncher.ui.tabs.models.st") as mock_st, patch(
            "llauncher.ui.tabs.models.render_model_registry"
        ) as mock_registry_render, patch(
            "llauncher.ui.tabs.models.render_add_model"
        ) as mock_add_form, patch(
            "llauncher.ui.tabs.models.render_model_card"
        ) as mock_card:
            self._patch_st(mock_st)

            render_models_tab(state, MagicMock(), MagicMock(), "local")

            mock_registry_render.assert_called_once()
            mock_add_form.assert_called_once_with(state)
            mock_card.assert_called_once()

    def test_editing_flag_routes_to_edit_form(self):
        """If session_state has editing_<name>=True, render the edit form."""
        from llauncher.ui.tabs.models import render_models_tab

        state = self._mk_state(models={"m1": MagicMock()})

        with patch("llauncher.ui.tabs.models.st") as mock_st, patch(
            "llauncher.ui.tabs.models.render_model_registry"
        ), patch(
            "llauncher.ui.tabs.models.render_edit_model"
        ) as mock_edit, patch(
            "llauncher.ui.tabs.models.render_add_model"
        ) as mock_add, patch(
            "llauncher.ui.tabs.models.render_model_card"
        ) as mock_card:
            self._patch_st(mock_st)
            mock_st.session_state.get.side_effect = (
                lambda key, default=None: True if key == "editing_m1" else default
            )

            render_models_tab(state, MagicMock(), MagicMock(), "local")

            mock_edit.assert_called_once_with(state, "m1")
            # Add form / cards must NOT render while editing.
            mock_add.assert_not_called()
            mock_card.assert_not_called()

    def test_local_target_shows_info_when_no_models(self):
        from llauncher.ui.tabs.models import render_models_tab

        state = self._mk_state(models={})

        with patch("llauncher.ui.tabs.models.st") as mock_st, patch(
            "llauncher.ui.tabs.models.render_model_registry"
        ), patch(
            "llauncher.ui.tabs.models.render_add_model"
        ), patch(
            "llauncher.ui.tabs.models.render_model_card"
        ) as mock_card:
            self._patch_st(mock_st)

            render_models_tab(state, MagicMock(), MagicMock(), "local")

            mock_card.assert_not_called()
            mock_st.info.assert_called()

    def test_remote_target_pulls_models_from_aggregator(self):
        from llauncher.ui.tabs.models import render_models_tab

        state = self._mk_state(models={})

        aggregator = MagicMock()
        # Two remote models on the target node + an unrelated model on
        # another node that must NOT be rendered.
        aggregator.get_all_models.return_value = {
            "gpu-rig": [{"name": "remote_a"}, {"name": "remote_b"}],
            "other-node": [{"name": "ignored"}],
        }
        aggregator.get_all_servers.return_value = []

        with patch("llauncher.ui.tabs.models.st") as mock_st, patch(
            "llauncher.ui.tabs.models.render_model_registry"
        ), patch(
            "llauncher.ui.tabs.models.render_add_model"
        ), patch(
            "llauncher.ui.tabs.models.render_model_card"
        ) as mock_card:
            self._patch_st(mock_st)

            render_models_tab(state, MagicMock(), aggregator, "gpu-rig")

            assert mock_card.call_count == 2
            rendered_names = [
                call.args[4]["name"] for call in mock_card.call_args_list
            ]
            assert sorted(rendered_names) == ["remote_a", "remote_b"]

    def test_running_server_is_handed_to_card(self):
        from llauncher.ui.tabs.models import render_models_tab
        from datetime import datetime

        running = MagicMock()
        running.pid = 1234
        running.port = 8080
        running.config_name = "m1"
        running.start_time = datetime.now()
        running.uptime_seconds.return_value = 30
        running.logs_path = "/tmp/logs"

        state = self._mk_state(
            models={"m1": MagicMock(to_dict=MagicMock(return_value={"name": "m1"}))},
            running={8080: running},
        )

        with patch("llauncher.ui.tabs.models.st") as mock_st, patch(
            "llauncher.ui.tabs.models.render_model_registry"
        ), patch(
            "llauncher.ui.tabs.models.render_add_model"
        ), patch(
            "llauncher.ui.tabs.models.render_model_card"
        ) as mock_card:
            self._patch_st(mock_st)

            render_models_tab(state, MagicMock(), MagicMock(), "local")

            assert mock_card.call_count == 1
            running_arg = mock_card.call_args_list[0].args[5]
            assert running_arg is not None
            assert running_arg.config_name == "m1"

    def test_target_string_is_required(self):
        """The dashboard's old ``selected_node=None`` branch is gone.

        ``render_models_tab`` accepts a string target; passing ``None``
        used to mean "all nodes" — that path was removed.
        """
        import inspect
        from llauncher.ui.tabs.models import render_models_tab

        sig = inspect.signature(render_models_tab)
        target_param = sig.parameters["target"]
        # Required string, no default. (No `=None`.) Annotation may be
        # ``str`` (resolved) or the string literal ``"str"`` depending on
        # ``from __future__ import annotations`` — accept both.
        assert target_param.default is inspect.Parameter.empty
        assert target_param.annotation in (str, "str")
