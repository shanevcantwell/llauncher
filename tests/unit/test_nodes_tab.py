"""Smoke tests for the Nodes tab Add Node form copy (#134, Phase 0).

These pin the operability strings shipped by the Phase 0 provisioning
docs (issue #134 / roadmap #137): the manual-token-copy info banner and
the platform-specific API Key help text. They use the same mock-``st``
pattern as ``test_models_tab.py`` — a real rendered-output smoke is
deferred to the Streamlit AppTest harness (#69).
"""

from unittest.mock import MagicMock, patch


class TestAddNodeFormProvisioningCopy:
    """Phase 0 (#134) banner + help-text strings on the Add Node form."""

    def _patch_st(self, mock_st):
        """Wire up the Streamlit mocks the form needs."""

        def mock_columns(n):
            count = len(n) if isinstance(n, list) else n
            return [MagicMock() for _ in range(count)]

        mock_st.columns.side_effect = mock_columns
        # Neither Test Connection nor Add Node is clicked.
        mock_st.form_submit_button.return_value = False
        return mock_st

    def _render(self, mock_st):
        from llauncher.ui.tabs.nodes import render_add_node_form

        self._patch_st(mock_st)
        render_add_node_form(MagicMock())

    def test_manual_token_copy_banner_renders(self):
        """An info banner surfaces the manual flow and its successor."""
        with patch("llauncher.ui.tabs.nodes.st") as mock_st:
            self._render(mock_st)

        mock_st.info.assert_called_once()
        banner = mock_st.info.call_args.args[0]
        assert "API token by hand" in banner
        assert "Adding a remote node" in banner  # README section pointer
        assert "#135" in banner  # session-token issuance successor

    def test_api_key_help_names_both_platform_commands(self):
        """First-contact help tells you what the field wants, per platform."""
        with patch("llauncher.ui.tabs.nodes.st") as mock_st:
            self._render(mock_st)

        api_key_calls = [
            call
            for call in mock_st.text_input.call_args_list
            if call.args and call.args[0] == "API Key"
        ]
        assert len(api_key_calls) == 1
        help_text = api_key_calls[0].kwargs["help"]
        assert "cat ~/.llauncher/agent.token" in help_text
        assert "Get-Content $env:USERPROFILE\\.llauncher\\agent.token" in help_text
        assert "ADR-003" in help_text
