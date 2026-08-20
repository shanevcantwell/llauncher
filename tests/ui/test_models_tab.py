"""Streamlit ``AppTest`` tests for the consolidated Models tab
(``llauncher/ui/tabs/models.py``, SP-5, #328).

## The pinned surface contract

The Models tab is a **composition root** (M4 Slice 13, #50): it owns no
verb of its own and delegates every model-shaped surface to exactly one
sub-renderer. Top to bottom, one page render promises:

1. The registry health table renders through
   ``model_registry.render_model_registry``, scoped to the sidebar's
   selected target node — always, on every render.
2. If the user is mid-edit (any ``editing_<name>`` session-state flag,
   armed by a card's Edit button), the **edit form replaces the rest of
   the page** via ``forms.render_edit_model`` — no add form, no cards —
   so the user cannot edit one model while starting another.
3. Otherwise an "Add New Model" expander hosts ``forms.render_add_model``
   (local-only config CRUD).
4. With no local models configured, an onboarding banner replaces the
   card grid; a remote target with no models gets no such banner (adding
   models is a local-node affordance).
5. Every model on the target gets exactly one ``model_card.render_model_card``
   call, in case-insensitive name order, with its running server (if any)
   already resolved — local cards from ``state``, remote cards from the
   ``RemoteAggregator`` facade, filtered to the target node.

Per the #330 parity ruling these tests assert the **dispatch seam** —
which sub-renderer fired, with what arguments — never widget cosmetics.
The sub-renderers' own behavior is pinned by their SP-3/SP-4/SP-6
siblings; here they are doubles patched on ``models.py``'s module
attributes (the names its ``from … import …`` bound).

Idiom (session-state pre-seed, shared with SP-4's ``test_forms.py``):
to land in the edit-mode short-circuit, build the harness with
``run=False``, set ``at.session_state["editing_<name>"] = True``, *then*
call the first ``at.run()`` — the flag must be in session state before
the script body executes, exactly as a rerun after the Edit click would
carry it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from llauncher.remote.node import RemoteServerInfo
from llauncher.ui.tabs.models import render_models_tab

PORT = 8123
PID = 4242


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _model_config(name):
    """A ``ModelConfig`` double answering the one method the tab calls."""
    cfg = MagicMock(name=f"ModelConfig[{name}]")
    cfg.to_dict.return_value = {"name": name, "model_path": f"/models/{name}.gguf"}
    return cfg


def _local_server(config_name, port=PORT, pid=PID):
    """A local ``state.running`` entry with the fields ``_build_running_map``
    reads to assemble its ``RemoteServerInfo``."""
    return SimpleNamespace(
        pid=pid,
        port=port,
        config_name=config_name,
        start_time=datetime(2026, 7, 16, tzinfo=timezone.utc),
        uptime_seconds=lambda: 61,
        logs_path=None,
    )


def _remote_server(node_name, config_name, port=PORT, pid=PID):
    return RemoteServerInfo(
        node_name=node_name,
        pid=pid,
        port=port,
        config_name=config_name,
        start_time="2026-07-16T00:00:00+00:00",
        uptime_seconds=61,
    )


def _card_calls(subs):
    """The ``(target, model_name, running_server)`` triple of each card call."""
    return [
        (c.args[3], c.args[4]["name"], c.args[5])
        for c in subs.render_model_card.call_args_list
    ]


# ---------------------------------------------------------------------------
# The dispatch seam: the four sub-renderers the composition root delegates
# to, doubled where ``models.py`` looks them up (its own module attributes,
# bound at import by ``from llauncher.ui.tabs.forms import …`` etc.).
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_sub_renders():
    with patch(
        "llauncher.ui.tabs.models.render_model_registry"
    ) as registry_table, patch(
        "llauncher.ui.tabs.models.render_add_model"
    ) as add_form, patch(
        "llauncher.ui.tabs.models.render_edit_model"
    ) as edit_form, patch(
        "llauncher.ui.tabs.models.render_model_card"
    ) as model_card:
        yield SimpleNamespace(
            render_model_registry=registry_table,
            render_add_model=add_form,
            render_edit_model=edit_form,
            render_model_card=model_card,
        )


def _tab(tab_harness, state, registry, aggregator, target="local", *, run=True):
    return tab_harness(
        render_models_tab, state, registry, aggregator, target, run=run
    )


# ---------------------------------------------------------------------------
# 1. Registry table — delegated on every render, scoped to the target
# ---------------------------------------------------------------------------
class TestRegistryTableDelegation:
    """The health table always renders through the registry renderer."""

    def test_registry_table_is_delegated_scoped_to_the_selected_target(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        at = _tab(tab_harness, mock_state, mock_registry, mock_aggregator, "local")

        assert not at.exception
        mock_sub_renders.render_model_registry.assert_called_once_with(
            mock_state, mock_registry, mock_aggregator, "local"
        )

    def test_registry_table_still_renders_while_an_edit_is_in_progress(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        # The short-circuit replaces everything *below* the table, not the
        # table itself — the user keeps their fleet overview while editing.
        mock_state.models["alpha"] = _model_config("alpha")

        at = _tab(
            tab_harness, mock_state, mock_registry, mock_aggregator, run=False
        )
        at.session_state["editing_alpha"] = True
        at.run()

        assert not at.exception
        mock_sub_renders.render_model_registry.assert_called_once_with(
            mock_state, mock_registry, mock_aggregator, "local"
        )


# ---------------------------------------------------------------------------
# 2. Edit-mode short-circuit — the editing_<name> flag replaces the page
# ---------------------------------------------------------------------------
class TestEditModeShortCircuit:
    """An armed ``editing_<name>`` flag swaps the page for that edit form."""

    def test_editing_flag_routes_the_page_to_that_models_edit_form(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        mock_state.models["alpha"] = _model_config("alpha")
        mock_state.models["beta"] = _model_config("beta")

        at = _tab(
            tab_harness, mock_state, mock_registry, mock_aggregator, run=False
        )
        at.session_state["editing_beta"] = True
        at.run()

        assert not at.exception
        mock_sub_renders.render_edit_model.assert_called_once_with(
            mock_state, "beta"
        )

    def test_edit_mode_suppresses_the_add_form_and_every_model_card(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        mock_state.models["alpha"] = _model_config("alpha")

        at = _tab(
            tab_harness, mock_state, mock_registry, mock_aggregator, run=False
        )
        at.session_state["editing_alpha"] = True
        at.run()

        assert not at.exception
        mock_sub_renders.render_add_model.assert_not_called()
        mock_sub_renders.render_model_card.assert_not_called()

    def test_without_an_editing_flag_the_edit_form_never_renders(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        mock_state.models["alpha"] = _model_config("alpha")

        at = _tab(tab_harness, mock_state, mock_registry, mock_aggregator)

        assert not at.exception
        mock_sub_renders.render_edit_model.assert_not_called()

    def test_a_stale_flag_for_an_unknown_model_does_not_hijack_the_page(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        # The flag scan iterates state.models — a leftover flag for a model
        # that no longer exists (e.g. deleted elsewhere) is inert and the
        # normal page renders.
        mock_state.models["alpha"] = _model_config("alpha")

        at = _tab(
            tab_harness, mock_state, mock_registry, mock_aggregator, run=False
        )
        at.session_state["editing_ghost"] = True
        at.run()

        assert not at.exception
        mock_sub_renders.render_edit_model.assert_not_called()
        mock_sub_renders.render_add_model.assert_called_once_with(mock_state)
        assert _card_calls(mock_sub_renders) == [("local", "alpha", None)]


# ---------------------------------------------------------------------------
# 3. Add New Model — expander hosting the add form
# ---------------------------------------------------------------------------
class TestAddModelExpander:
    """The add form renders inside its expander on the normal page."""

    def test_add_form_is_delegated_inside_the_add_new_model_expander(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        at = _tab(tab_harness, mock_state, mock_registry, mock_aggregator)

        assert not at.exception
        mock_sub_renders.render_add_model.assert_called_once_with(mock_state)
        assert any("Add New Model" in exp.label for exp in at.expander)


# ---------------------------------------------------------------------------
# 4. Empty state — onboarding banner, local target only
# ---------------------------------------------------------------------------
class TestEmptyState:
    """No local models → onboarding banner instead of a card grid."""

    def test_no_local_models_shows_the_onboarding_banner_and_no_cards(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        at = _tab(tab_harness, mock_state, mock_registry, mock_aggregator, "local")

        assert not at.exception
        assert any("No models configured" in info.value for info in at.info)
        mock_sub_renders.render_model_card.assert_not_called()

    def test_empty_remote_target_gets_no_onboarding_banner(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        # The banner points at the local-only Add form; on a remote target
        # (no local models either) it would be a wrong instruction, so the
        # page proceeds to an empty card grid instead.
        at = _tab(
            tab_harness, mock_state, mock_registry, mock_aggregator, "gpu-rig"
        )

        assert not at.exception
        assert not any("No models configured" in info.value for info in at.info)
        mock_sub_renders.render_model_card.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Per-model-card loop — sorted iteration, resolved running servers
# ---------------------------------------------------------------------------
class TestModelCardLoop:
    """Every model on the target gets one card, in name order, with its
    running server pre-resolved."""

    def test_each_local_model_gets_one_card_in_case_insensitive_name_order(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        for name in ("Bravo", "alpha", "Charlie"):
            mock_state.models[name] = _model_config(name)

        at = _tab(tab_harness, mock_state, mock_registry, mock_aggregator, "local")

        assert not at.exception
        assert _card_calls(mock_sub_renders) == [
            ("local", "alpha", None),
            ("local", "Bravo", None),
            ("local", "Charlie", None),
        ]

    def test_running_local_server_reaches_its_own_card_and_no_other(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        mock_state.models["alpha"] = _model_config("alpha")
        mock_state.models["beta"] = _model_config("beta")
        mock_state.running[PORT] = _local_server("beta")

        at = _tab(tab_harness, mock_state, mock_registry, mock_aggregator, "local")

        assert not at.exception
        (alpha_call, beta_call) = mock_sub_renders.render_model_card.call_args_list
        assert alpha_call.args[4]["name"] == "alpha"
        assert alpha_call.args[5] is None
        assert beta_call.args[4]["name"] == "beta"
        resolved = beta_call.args[5]
        assert isinstance(resolved, RemoteServerInfo)
        assert (resolved.node_name, resolved.config_name, resolved.port, resolved.pid) == (
            "local", "beta", PORT, PID
        )
        # The lookup reads the *same* scan the earlier registry-table render
        # already took (#370) — it must not trigger its own redundant
        # ``state.refresh()``. (``render_model_registry`` is mocked here, so
        # any call surfacing on ``mock_state.refresh`` would have to come
        # from ``_build_running_map`` itself.)
        mock_state.refresh.assert_not_called()

    def test_remote_target_cards_come_from_the_aggregator_not_local_state(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        # Local state has its own models — none of them may leak onto a
        # remote target's page. Remote models arrive both as raw dicts and
        # as to_dict()-bearing objects; the card always receives the dict.
        mock_state.models["local-only"] = _model_config("local-only")
        mock_aggregator.get_all_models.return_value = {
            "gpu-rig": [{"name": "zeta"}, _model_config("Echo")],
            "other-rig": [{"name": "not-our-target"}],
        }

        at = _tab(
            tab_harness, mock_state, mock_registry, mock_aggregator, "gpu-rig"
        )

        assert not at.exception
        assert _card_calls(mock_sub_renders) == [
            ("gpu-rig", "Echo", None),
            ("gpu-rig", "zeta", None),
        ]

    def test_remote_running_servers_are_filtered_to_the_target_node(
        self, tab_harness, mock_state, mock_registry, mock_aggregator,
        mock_sub_renders,
    ):
        mock_aggregator.get_all_models.return_value = {
            "gpu-rig": [{"name": "zeta"}]
        }
        ours = _remote_server("gpu-rig", "zeta")
        mock_aggregator.get_all_servers.return_value = [
            _remote_server("other-rig", "zeta", port=9999, pid=1),
            ours,
        ]

        at = _tab(
            tab_harness, mock_state, mock_registry, mock_aggregator, "gpu-rig"
        )

        assert not at.exception
        assert _card_calls(mock_sub_renders) == [("gpu-rig", "zeta", ours)]
