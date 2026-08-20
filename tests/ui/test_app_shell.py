"""Streamlit ``AppTest`` tests for the app shell (``llauncher/ui/app.py``).

Phase 2b (test-coverage-plan.md) pin: 2026-08-20 review finding. These two
tests are deliberately driven through the **real** ``streamlit.testing.v1
.AppTest`` runtime (not a mocked ``st``, as ``tests/unit/test_ui_app.py``
uses) because both pin Streamlit-internal behavior the app relies on, not
just llauncher's own call sequencing:

- ``st.stop()`` actually halting the script mid-run (the agent-down branch
  must never reach the tab-mount code below it).
- ``st.rerun()``'s "one-shot effect" semantics — a toast queued just before
  a rerun must still be visible on the *next* run's ``at.toast`` (companion
  to #335/#448). A mocked ``st`` cannot observe either fact; only AppTest's
  real script-run loop can. A future streamlit version bump that breaks
  either invariant goes red here, not in production.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest


def _main_script():  # pragma: no cover - exec'd by AppTest
    from llauncher.ui.app import main

    main()


@pytest.fixture
def app_harness():
    """Build an ``AppTest`` around the real ``app.main()`` entry point.

    Callers patch ``llauncher.ui.app.get_state`` / ``get_registry`` /
    ``get_aggregator`` / ``is_agent_ready`` (and, for the agent-up path, the
    four tab renderers) before calling ``at.run()`` — the same seams
    ``tests/unit/test_ui_app.py::TestMainTabs`` patches, but exercised
    through real Streamlit instead of a mocked ``st``.
    """
    return AppTest.from_function(_main_script, default_timeout=6)


class TestAgentDownHaltsBeforeTabs:
    """``main()``'s agent-down branch (``app.py`` lines ~116-118): the
    banner renders and ``st.stop()`` halts the script before any tab or
    sidebar control mounts.
    """

    def test_agent_down_shows_banner_and_stops_before_sidebar_and_tabs(
        self, app_harness
    ):
        registry = MagicMock(name="NodeRegistry")

        with patch("llauncher.ui.app.get_state", return_value=MagicMock()), \
             patch("llauncher.ui.app.get_registry", return_value=registry), \
             patch("llauncher.ui.app.get_aggregator", return_value=MagicMock()), \
             patch("llauncher.ui.app.is_agent_ready", return_value=False):
            app_harness.run()

        assert not app_harness.exception
        # The page title still renders (chrome lives above the check).
        assert app_harness.title[0].value == "🚀 llauncher"
        # The banner's error text is present...
        assert any(
            "Local agent is not running" in e.value for e in app_harness.error
        )
        # ...and st.stop() means nothing past it ran: no sidebar Refresh
        # button, no tab navigation, and the registry's own I/O verbs were
        # never reached.
        assert app_harness.button == []
        assert app_harness.tabs == []
        registry.refresh_all.assert_not_called()


class TestRefreshAllToastSurvivesRerun:
    """``main()``'s sidebar "Refresh All" control (``app.py`` lines
    ~126-130): clicking it dispatches ``state.refresh()`` /
    ``registry.refresh_all()``, queues a confirmation toast, and calls
    ``st.rerun()`` — all folded into one ``at.run()`` by AppTest's rerun
    discipline (the same idiom ``tests/ui/test_nodes_tab.py`` pins for the
    Nodes tab's own Refresh All). This is the app-shell twin of that
    control, and additionally asserts the toast text survives onto the
    *next* script run rather than being dropped by the rerun.
    """

    def test_refresh_all_click_dispatches_and_toast_survives_rerun(
        self, app_harness
    ):
        state = MagicMock(name="LauncherState")
        registry = MagicMock(name="NodeRegistry")
        aggregator = MagicMock(name="RemoteAggregator")

        with patch("llauncher.ui.app.get_state", return_value=state), \
             patch("llauncher.ui.app.get_registry", return_value=registry), \
             patch("llauncher.ui.app.get_aggregator", return_value=aggregator), \
             patch("llauncher.ui.app.is_agent_ready", return_value=True), \
             patch("llauncher.ui.app.render_node_selector", return_value="local"), \
             patch("llauncher.ui.tabs.dashboard.render_dashboard"), \
             patch("llauncher.ui.tabs.models.render_models_tab"), \
             patch("llauncher.ui.tabs.nodes.render_nodes_tab"), \
             patch("llauncher.ui.tabs.audit.render_audit_tab"):
            app_harness.run()

            assert not app_harness.exception
            assert not state.refresh.called
            assert not registry.refresh_all.called

            refresh_button = next(
                b for b in app_harness.button if b.label == "🔄 Refresh All"
            )
            refresh_button.click()
            app_harness.run()

        assert not app_harness.exception
        state.refresh.assert_called_once()
        registry.refresh_all.assert_called_once()
        # The toast queued right before st.rerun() is still visible on the
        # run that follows it — pinning has_one_shot_effect (#335/#448).
        toast_bodies = [t.body for t in app_harness.toast]
        assert any("Refreshed all nodes" in b for b in toast_bodies)
