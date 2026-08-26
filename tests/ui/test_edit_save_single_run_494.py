"""AppTest-level regression tests for issue #494.

The defect: Edit / Cancel / Save (and Delete) each did
``if st.button(...): mutate session_state; st.rerun()``. The click already
triggers one script run; the explicit ``st.rerun()`` inside the click
branch aborted that run and started a second — and every script run pays
``model_registry.py``'s unconditional ``state.refresh()`` (two full
``psutil`` process-table scans), so Edit/Save cost twice what any other
interaction did.

Unlike ``test_forms.py`` / ``test_model_card.py``, which each drive a
single sub-renderer with everything above/below it mocked, this module
mounts the real composition — ``render_models_tab`` calling the real
``model_registry.render_model_registry``, ``forms.render_edit_model`` and
``model_card.render_model_card`` — because the bug lived exactly in how
those pieces interleave: ``models.py``'s ``editing_model`` routing check
runs *before* the card grid (and its Edit button) renders, so a plain
in-body flag mutation on click could not have rerouted the page within a
single run even with the ``st.rerun()`` removed — only an ``on_click``
callback (which runs before the script body starts) can.

``LauncherState.refresh()`` is a plain attribute of the ``mock_state``
MagicMock fixture, so its own ``call_count`` *is* the counter the #494
contract calls for ("wrap it with a counter via monkeypatch") — no
separate wrapper needed. Since AppTest's ``at.run()`` transparently chases
an internal ``st.rerun()`` to completion in the same call (the idiom
``test_model_card.py``'s ``_click_and_run`` docstring names), a
``refresh()`` call count of more than 1 after one click + one ``at.run()``
is exactly the signature a reintroduced double-run would leave behind.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from llauncher.ui.tabs.models import render_models_tab


@pytest.fixture
def port_is_free():
    """Patch the port picker's unmanaged-collision probe to 'free'.

    Module-local copy of ``test_model_card.py``'s fixture of the same name
    (not shared via ``conftest.py``) — every card in this module's grid
    renders ``_render_start_button``/the port picker even though these
    tests never click Start.
    """
    with patch(
        "llauncher.ui.components.port_picker.is_port_in_use", return_value=False
    ) as fn:
        yield fn


def _button_by_label(at, label):
    for el in at.button:
        if el.label == label:
            return el
    raise AssertionError(
        f"no button labelled {label!r}; saw {[e.label for e in at.button]}"
    )


@pytest.fixture
def edit_save_state(mock_state, tmp_path):
    """``mock_state`` seeded with one real, on-disk-backed local model.

    A real ``ModelConfig`` (not a ``MagicMock``) is required here: unlike
    the dispatch-seam tests in ``test_model_card.py``, this module drives
    the *real* ``forms.render_edit_model``, whose widgets read
    ``config.model_path`` / ``.n_gpu_layers`` / etc. as concrete
    str/int/bool values (a ``MagicMock`` attribute would fail Streamlit's
    widget-value type checks).
    """
    from llauncher.models.config import ModelConfig

    model_path = tmp_path / "model.gguf"
    model_path.touch()
    config = ModelConfig(name="existing-model", model_path=str(model_path))
    mock_state.models["existing-model"] = config
    return mock_state


def _tab(tab_harness, state, registry, aggregator, *, run=True):
    return tab_harness(
        render_models_tab, state, registry, aggregator, "local", run=run
    )


class TestEditSingleRun:
    """Clicking Edit must land the edit form in exactly one script run,
    paying exactly one ``state.refresh()``.
    """

    def test_edit_click_causes_one_refresh_and_routes_to_the_form(
        self, tab_harness, edit_save_state, mock_registry, mock_aggregator,
        mock_config_store, port_is_free,
    ):
        at = _tab(tab_harness, edit_save_state, mock_registry, mock_aggregator)
        edit_save_state.refresh.reset_mock()

        at.button(key="edit_local_existing-model_enabled").click()
        at.run()

        assert not at.exception
        assert edit_save_state.refresh.call_count == 1
        assert at.session_state["editing_existing-model"] is True
        assert any(
            "Edit Model: existing-model" in s.value for s in at.subheader
        )


class TestSaveSingleRun:
    """Clicking Save Changes must persist, route back to the card grid,
    and show the confirmation toast, all within one script run paying
    exactly one ``state.refresh()``.
    """

    def test_save_click_causes_one_refresh_routes_back_and_toasts(
        self, tab_harness, edit_save_state, mock_registry, mock_aggregator,
        mock_config_store, port_is_free,
    ):
        mock_config_store.load.return_value = {
            "existing-model": edit_save_state.models["existing-model"]
        }

        at = _tab(
            tab_harness, edit_save_state, mock_registry, mock_aggregator,
            run=False,
        )
        at.session_state["editing_existing-model"] = True
        at.run()
        edit_save_state.refresh.reset_mock()

        _button_by_label(at, "Save Changes").click()
        at.run()

        assert not at.exception
        # One script run, one refresh() — the #494 measurement.
        assert edit_save_state.refresh.call_count == 1
        # Routed back to the card grid within that same run (no lingering
        # edit form) rather than needing a further interaction.
        assert not any("Edit Model" in s.value for s in at.subheader)
        assert "editing_existing-model" not in at.session_state
        # The confirmation survives into this same rendered run (operator
        # constraint) — not merely queued in session_state for a run that
        # never comes.
        assert any(
            "Saved config for existing-model" in t.body for t in at.toast
        )
        mock_config_store.update_model.assert_called_once()

    def test_save_validation_failure_leaves_a_sticky_error_not_a_toast(
        self, tab_harness, edit_save_state, mock_registry, mock_aggregator,
        mock_config_store, port_is_free,
    ):
        """A failed Save must not silently vanish (operator constraint):
        the form stays open with a sticky ``st.error()``, not a toast.
        """
        at = _tab(
            tab_harness, edit_save_state, mock_registry, mock_aggregator,
            run=False,
        )
        at.session_state["editing_existing-model"] = True
        at.run()

        for el in at.text_input:
            if el.label == "Model Path":
                el.set_value("")
        _button_by_label(at, "Save Changes").click()
        at.run()

        assert not at.exception
        # Still on the edit form (error path never clears editing_).
        assert "editing_existing-model" in at.session_state
        assert any("required" in e.value for e in at.error)
        assert not at.toast
        mock_config_store.update_model.assert_not_called()
