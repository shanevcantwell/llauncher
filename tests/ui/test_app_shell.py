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

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from llauncher.models.validation import ValidationReport


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

    #497's hoisted ``state.refresh()`` sits *below* this gate, so an
    agent-down run keeps paying zero ``psutil`` process-table walks —
    pinned here so a future re-hoist above the gate goes red.
    """

    def test_agent_down_shows_banner_and_stops_before_sidebar_and_tabs(
        self, app_harness
    ):
        registry = MagicMock(name="NodeRegistry")
        state = MagicMock(name="LauncherState")

        with patch("llauncher.ui.app.get_state", return_value=state), \
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
        # ...including the hoisted per-run refresh: an agent-down run
        # renders only a banner and must not walk the process table.
        state.refresh.assert_not_called()


def _corrupt_config_error() -> json.JSONDecodeError:
    """The path-bearing JSONDecodeError ``ConfigStore.load()`` raises on a
    corrupt ``config.json`` (issue #472's fail-loud contract)."""
    return json.JSONDecodeError(
        "Corrupt config at /home/op/.llauncher/config.json: Expecting value",
        doc="{not json",
        pos=0,
    )


def _unreadable_config_error() -> OSError:
    """The path-bearing OSError ``ConfigStore.load()`` raises on an
    unreadable ``config.json``."""
    return OSError(
        "Cannot read config at /home/op/.llauncher/config.json: "
        "[Errno 13] Permission denied"
    )


class TestConfigErrorBannerOnStateBuild:
    """Issue #476: ``main()``'s state build (``get_state()`` →
    ``LauncherState()`` → ``ConfigStore.load()``) fails loud on a corrupt
    or unreadable config per #472. The UI must surface that as an
    ``st.error`` banner carrying the exception's path-bearing message and
    ``st.stop()`` — never a raw Streamlit traceback (``at.exception``),
    and nothing past the banner (agent check, sidebar, tabs) may render.
    """

    @pytest.mark.parametrize(
        "raised",
        [_corrupt_config_error(), _unreadable_config_error()],
        ids=["corrupt-json", "unreadable-oserror"],
    )
    def test_config_load_failure_shows_banner_not_traceback(
        self, app_harness, raised
    ):
        registry = MagicMock(name="NodeRegistry")

        with patch("llauncher.ui.app.get_state", side_effect=raised), \
             patch("llauncher.ui.app.get_registry", return_value=registry), \
             patch("llauncher.ui.app.get_aggregator", return_value=MagicMock()), \
             patch("llauncher.ui.app.is_agent_ready") as agent_check:
            app_harness.run()

        # The load failure surfaces as a banner, not an uncaught exception.
        assert not app_harness.exception
        # Page chrome still renders (title lives above the state build).
        assert app_harness.title[0].value == "🚀 llauncher"
        # The banner carries the exception's path-bearing message — the
        # operator sees WHICH file to fix.
        banner_texts = [e.value for e in app_harness.error]
        assert any("Model config could not be loaded" in t for t in banner_texts)
        assert any("/home/op/.llauncher/config.json" in t for t in banner_texts)
        # st.stop() halted the script before anything else mounted.
        assert app_harness.button == []
        assert app_harness.tabs == []
        agent_check.assert_not_called()


class TestConfigErrorBannerOnHoistedRefresh:
    """#497's hoisted per-run ``state.refresh()`` is a third site that
    re-reads ``config.json``, so it carries the same #476 fail-loud
    wrapper as the state build and the sidebar handler: a config that is
    corrupt when the hoisted call runs must banner-and-stop before the
    sidebar and the tabs mount, not raise a traceback.
    """

    def test_hoisted_refresh_failure_banners_and_stops_before_sidebar(
        self, app_harness
    ):
        state = MagicMock(name="LauncherState")
        state.refresh.side_effect = _unreadable_config_error()
        registry = MagicMock(name="NodeRegistry")

        with patch("llauncher.ui.app.get_state", return_value=state),              patch("llauncher.ui.app.get_registry", return_value=registry),              patch("llauncher.ui.app.get_aggregator", return_value=MagicMock()),              patch("llauncher.ui.app.is_agent_ready", return_value=True),              patch("llauncher.ui.app.render_node_selector", return_value="local"):
            app_harness.run()

        assert not app_harness.exception
        state.refresh.assert_called_once()
        banner_texts = [e.value for e in app_harness.error]
        assert any("Model config could not be loaded" in t for t in banner_texts)
        assert any("/home/op/.llauncher/config.json" in t for t in banner_texts)
        # st.stop() halted the run before the sidebar control and tabs.
        assert app_harness.button == []
        assert app_harness.tabs == []
        registry.refresh_all.assert_not_called()


class TestConfigErrorBannerOnRefreshClick:
    """Issue #476, second un-wrapped site: the sidebar "Refresh All"
    control's ``state.refresh()`` re-reads ``config.json``. A config that
    goes corrupt at the moment of the click must banner-and-stop inside
    that handler — before ``registry.refresh_all()`` or the success
    toast — not explode into a traceback.

    #497 added a hoisted per-run ``state.refresh()`` above the sidebar;
    this test deliberately lets both hoisted calls succeed so the
    handler's own call is the one that fails, keeping the handler's
    except-branch (``app.py``'s ``show_config_error_banner``/``st.stop``
    inside the button branch) the site under test.
    """

    def test_refresh_click_on_corrupt_config_banners_and_stops(
        self, app_harness
    ):
        state = MagicMock(name="LauncherState")
        # #497: main() now calls state.refresh() once per run, hoisted
        # ahead of the sidebar. To keep this test on *its* contract --
        # the sidebar handler's own un-wrapped refresh site (#476) -- the
        # first run's hoisted refresh and the click run's hoisted refresh
        # both succeed; the third call, the handler's own, is the one
        # that raises. Anything else and the hoisted call would be the
        # raiser and app.py's sidebar except-branch would go untested.
        state.refresh.side_effect = [None, None, _corrupt_config_error()]
        registry = MagicMock(name="NodeRegistry")

        with patch("llauncher.ui.app.get_state", return_value=state), \
             patch("llauncher.ui.app.get_registry", return_value=registry), \
             patch("llauncher.ui.app.get_aggregator", return_value=MagicMock()), \
             patch("llauncher.ui.app.is_agent_ready", return_value=True), \
             patch("llauncher.ui.app.render_node_selector", return_value="local"), \
             patch("llauncher.ui.tabs.dashboard.render_dashboard"), \
             patch("llauncher.ui.tabs.models.render_models_tab"), \
             patch("llauncher.ui.tabs.nodes.render_nodes_tab"), \
             patch("llauncher.ui.tabs.audit.render_audit_tab"):
            app_harness.run()
            assert not app_harness.exception

            refresh_button = next(
                b for b in app_harness.button if b.label == "🔄 Refresh All"
            )
            refresh_button.click()
            app_harness.run()

        assert not app_harness.exception
        # Hoisted refresh on the healthy first run, hoisted refresh on
        # the click's run, then the sidebar handler's own refresh --
        # which raises. Three calls, and the banner below comes from the
        # handler's except-branch, not the hoisted one.
        assert state.refresh.call_count == 3
        # st.stop() fired before the registry fan-out and the toast.
        registry.refresh_all.assert_not_called()
        assert app_harness.toast == []
        banner_texts = [e.value for e in app_harness.error]
        assert any("Model config could not be loaded" in t for t in banner_texts)
        assert any("/home/op/.llauncher/config.json" in t for t in banner_texts)


class TestRefreshAllClickIsSingleRun:
    """``main()``'s sidebar "Refresh All" control: clicking it dispatches
    ``state.refresh()`` / ``registry.refresh_all()`` and shows a
    confirmation toast, all within the ONE script run the click itself
    causes (issue #498) -- no explicit ``st.rerun()``. Before #498 the
    handler ended in an explicit ``st.rerun()``, forcing a second full
    run (and a third ``state.refresh()`` call, on top of #497's hoisted
    per-run one) just to reflect a click that already reruns the script
    on its own.
    """

    def test_refresh_all_click_dispatches_and_toasts_in_one_run(
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
            # #497: the hoisted per-run refresh already ran once here,
            # ahead of any click. Reset so the assertions below isolate
            # what the refresh-button click itself adds.
            assert state.refresh.call_count == 1
            assert not registry.refresh_all.called
            state.refresh.reset_mock()

            refresh_button = next(
                b for b in app_harness.button if b.label == "🔄 Refresh All"
            )
            refresh_button.click()
            app_harness.run()

        assert not app_harness.exception
        # #498: exactly one script run for this click, so this click's
        # own run pays only the hoisted refresh at the top of main() plus
        # the sidebar handler's own explicit refresh() -- no third,
        # rerun-chased call.
        assert state.refresh.call_count == 2
        registry.refresh_all.assert_called_once()
        # The toast is visible in the same run the click produced (no
        # rerun needed to surface it).
        toast_bodies = [t.body for t in app_harness.toast]
        assert any("Refreshed all nodes" in b for b in toast_bodies)

    def test_refresh_all_click_executes_main_exactly_once(self, app_harness):
        """Direct proof of the #498 fix: wraps ``app.main`` in a
        call-counting double (the same idiom ``test_model_card.py``'s
        ``TestSingleScriptRunPerClick498`` and ``test_nodes_tab.py``'s
        twin use) so its call count *is* the number of full script
        executions one click produced. ``_main_script`` re-imports
        ``main`` from the module on every run, so patching the module
        attribute here is visible to every run AppTest performs.
        """
        import llauncher.ui.app as app_module

        state = MagicMock(name="LauncherState")
        registry = MagicMock(name="NodeRegistry")

        with patch.object(app_module, "main", wraps=app_module.main) as counted, \
             patch("llauncher.ui.app.get_state", return_value=state), \
             patch("llauncher.ui.app.get_registry", return_value=registry), \
             patch("llauncher.ui.app.get_aggregator", return_value=MagicMock()), \
             patch("llauncher.ui.app.is_agent_ready", return_value=True), \
             patch("llauncher.ui.app.render_node_selector", return_value="local"), \
             patch("llauncher.ui.tabs.dashboard.render_dashboard"), \
             patch("llauncher.ui.tabs.models.render_models_tab"), \
             patch("llauncher.ui.tabs.nodes.render_nodes_tab"), \
             patch("llauncher.ui.tabs.audit.render_audit_tab"):
            app_harness.run()
            before = counted.call_count

            refresh_button = next(
                b for b in app_harness.button if b.label == "🔄 Refresh All"
            )
            refresh_button.click()
            app_harness.run()

        assert not app_harness.exception
        assert counted.call_count == before + 1


class TestHoistedRefreshRunsOnceAcrossAllTabs:
    """Issue #497: one ``state.refresh()`` per script run, full stop.

    ``dashboard.py`` and ``model_registry.py`` used to each call
    ``state.refresh()`` unconditionally, and ``st.tabs`` executes every
    tab body every run, so a steady-state run paid two full psutil
    process-table walks per call, four per interaction. This test mounts
    the real Dashboard and Models tabs (the two former call sites,
    Models pulling in the real ``model_registry.render_model_registry``)
    alongside the real app shell and asserts the fixture's ``state``
    (a single shared object across every tab, exactly as production
    passes it) sees ``refresh()`` called exactly once for the run.
    """

    def test_one_refresh_per_run_with_real_dashboard_and_models_tabs(
        self, app_harness, mock_state, mock_registry, mock_aggregator,
    ):
        report = ValidationReport(
            checked_at=datetime.now(timezone.utc), ok=True, models=[]
        )

        with patch("llauncher.ui.app.get_state", return_value=mock_state), \
             patch("llauncher.ui.app.get_registry", return_value=mock_registry), \
             patch("llauncher.ui.app.get_aggregator", return_value=mock_aggregator), \
             patch("llauncher.ui.app.is_agent_ready", return_value=True), \
             patch("llauncher.ui.app.render_node_selector", return_value="local"), \
             patch("llauncher.operations.validate_models", return_value=report), \
             patch("llauncher.ui.tabs.nodes.render_nodes_tab"), \
             patch("llauncher.ui.tabs.audit.render_audit_tab"):
            app_harness.run()

        assert not app_harness.exception
        # Exactly one refresh for the whole run -- app.py's hoisted call
        # -- even though both the real Dashboard tab (formerly
        # dashboard.py:54) and the real Models tab (formerly
        # model_registry.py:48, reached via render_models_tab) executed
        # their bodies this run.
        mock_state.refresh.assert_called_once()
