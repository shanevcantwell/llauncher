"""Streamlit ``AppTest`` tests for the model add/edit forms
(``llauncher/ui/tabs/forms.py``, SP-4, #328).

Per the #330 parity audit, add/edit-model is a **documented, out-of-scope
special case**: unlike ``start``/``stop``/``swap``/``delete``, there is no
``ops.*`` orchestration verb for model CRUD — both forms call
``ConfigStore.add_model`` / ``update_model`` directly (refs #292/#152). This
module does not fix that; it pins the *current* dispatch precisely — the
exact ``ConfigStore`` classmethod each submit path calls, with what argument
shape — so a future ``ops.add_model``/``ops.update_model`` mint (once it
exists) has a byte-for-byte "before" contract to diff against, and any
accidental behavior drift (e.g. edit silently re-adding instead of updating)
fails a named test rather than passing unnoticed.

Idiom: batch the form's inputs via ``set_value``/``set_checked``/``select``,
click the ``st.form_submit_button``, call ``at.run()``, then assert on the
rendered output (``at.success`` / ``at.error``) and on the ``ConfigStore``
call captured by the ``mock_config_store`` fixture (conftest.py, owned by
this work item).
"""

from __future__ import annotations

import pytest

from llauncher.ui.tabs.forms import render_add_model, render_edit_model


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _text_input_by_label(at, label):
    for el in at.text_input:
        if el.label == label:
            return el
    raise AssertionError(
        f"no text_input labelled {label!r}; saw {[e.label for e in at.text_input]}"
    )


def _button_by_label(at, label):
    for el in at.button:
        if el.label == label:
            return el
    raise AssertionError(
        f"no button labelled {label!r}; saw {[e.label for e in at.button]}"
    )


@pytest.fixture
def model_path(tmp_path):
    """A real, existing file path — ``ModelConfig`` validates path existence."""
    path = tmp_path / "model.gguf"
    path.touch()
    return str(path)


@pytest.fixture
def existing_config(model_path):
    """A real ``ModelConfig`` for the model being edited."""
    from llauncher.models.config import ModelConfig

    return ModelConfig(name="existing-model", model_path=model_path)


# ---------------------------------------------------------------------------
# Add-model form
# ---------------------------------------------------------------------------
class TestAddModelSuccess:
    """A valid, unique name + path adds the model through ``ConfigStore``."""

    def test_valid_submission_calls_config_store_add_model(
        self, tab_harness, mock_state, mock_config_store, model_path
    ):
        at = tab_harness(render_add_model, mock_state)

        _text_input_by_label(at, "Model Name").set_value("new-model")
        _text_input_by_label(at, "Model Path").set_value(model_path)
        _button_by_label(at, "Add Model").click()
        at.run()

        assert not at.exception
        mock_config_store.add_model.assert_called_once()
        (added_config,), kwargs = mock_config_store.add_model.call_args
        assert added_config.name == "new-model"
        assert added_config.model_path == model_path
        assert kwargs["caller"] == "ui"

    def test_valid_submission_shows_success_and_updates_state(
        self, tab_harness, mock_state, mock_config_store, model_path
    ):
        at = tab_harness(render_add_model, mock_state)

        _text_input_by_label(at, "Model Name").set_value("new-model")
        _text_input_by_label(at, "Model Path").set_value(model_path)
        _button_by_label(at, "Add Model").click()
        at.run()

        assert not at.exception
        assert any("Added model 'new-model'" in s.value for s in at.success)
        assert "new-model" in mock_state.models


class TestAddModelDuplicateNameRejected:
    """A name already present in ``state.models`` is rejected before ``ConfigStore``."""

    def test_duplicate_name_shows_error_and_skips_config_store(
        self, tab_harness, mock_state, mock_config_store, existing_config, model_path
    ):
        mock_state.models["existing-model"] = existing_config
        at = tab_harness(render_add_model, mock_state)

        _text_input_by_label(at, "Model Name").set_value("existing-model")
        _text_input_by_label(at, "Model Path").set_value(model_path)
        _button_by_label(at, "Add Model").click()
        at.run()

        assert not at.exception
        assert any("already exists" in e.value for e in at.error)
        mock_config_store.add_model.assert_not_called()


class TestAddModelMissingRequiredField:
    """Blank name or path is rejected client-side, before ``ConfigStore``."""

    def test_missing_model_path_shows_error_and_skips_config_store(
        self, tab_harness, mock_state, mock_config_store
    ):
        at = tab_harness(render_add_model, mock_state)

        _text_input_by_label(at, "Model Name").set_value("new-model")
        # Model Path left blank.
        _button_by_label(at, "Add Model").click()
        at.run()

        assert not at.exception
        assert any("required" in e.value for e in at.error)
        mock_config_store.add_model.assert_not_called()

    def test_missing_name_shows_error_and_skips_config_store(
        self, tab_harness, mock_state, mock_config_store, model_path
    ):
        at = tab_harness(render_add_model, mock_state)

        # Model Name left blank.
        _text_input_by_label(at, "Model Path").set_value(model_path)
        _button_by_label(at, "Add Model").click()
        at.run()

        assert not at.exception
        assert any("required" in e.value for e in at.error)
        mock_config_store.add_model.assert_not_called()


class TestEditModelDiscoversModelNameFromSessionState:
    """``render_edit_model(state)`` (no explicit ``model_name``) — the shape
    ``models.py`` actually calls in production — finds the one model with an
    ``editing_<name>`` session-state flag set.
    """

    def test_no_editing_flag_set_renders_nothing(self, tab_harness, mock_state, existing_config):
        mock_state.models["existing-model"] = existing_config

        at = tab_harness(render_edit_model, mock_state)

        assert not at.exception
        assert not at.subheader

    def test_editing_flag_set_renders_that_models_form(
        self, tab_harness, mock_state, existing_config
    ):
        mock_state.models["existing-model"] = existing_config

        at = tab_harness(render_edit_model, mock_state, run=False)
        at.session_state["editing_existing-model"] = True
        at.run()

        assert not at.exception
        assert "Edit Model: existing-model" in at.subheader[0].value


class TestAddModelConfigStoreFailureShowsErrorNotException:
    """A ``ConfigStore.add_model`` failure surfaces as ``st.error``, never an
    uncaught exception — the form's error boundary around the ``ops``-less
    write path (#330 audit item 3).
    """

    def test_config_store_raises_shows_error(
        self, tab_harness, mock_state, mock_config_store, model_path
    ):
        mock_config_store.add_model.side_effect = OSError("disk full")
        at = tab_harness(render_add_model, mock_state)

        _text_input_by_label(at, "Model Name").set_value("new-model")
        _text_input_by_label(at, "Model Path").set_value(model_path)
        _button_by_label(at, "Add Model").click()
        at.run()

        assert not at.exception
        assert any("Error adding model" in e.value for e in at.error)
        assert "new-model" not in mock_state.models


# ---------------------------------------------------------------------------
# Edit-model form
# ---------------------------------------------------------------------------
class TestEditModelSuccess:
    """Editing an already-persisted model calls ``ConfigStore.update_model``."""

    def test_valid_submission_calls_config_store_update_model(
        self, tab_harness, mock_state, mock_config_store, existing_config, tmp_path
    ):
        mock_state.models["existing-model"] = existing_config
        mock_config_store.load.return_value = {"existing-model": existing_config}

        new_path = tmp_path / "updated.gguf"
        new_path.touch()

        at = tab_harness(
            render_edit_model, mock_state, "existing-model", run=False
        )
        at.session_state["editing_existing-model"] = True
        at.run()

        _text_input_by_label(at, "Model Path").set_value(str(new_path))
        _button_by_label(at, "Save Changes").click()
        at.run()

        assert not at.exception
        mock_config_store.update_model.assert_called_once()
        (name_arg, updated_config), kwargs = mock_config_store.update_model.call_args
        assert name_arg == "existing-model"
        assert updated_config.model_path == str(new_path)
        assert kwargs["caller"] == "ui"
        assert any("Updated model 'existing-model'" in s.value for s in at.success)

    def test_valid_submission_updates_state_and_clears_editing_flag(
        self, tab_harness, mock_state, mock_config_store, existing_config
    ):
        mock_state.models["existing-model"] = existing_config
        mock_config_store.load.return_value = {"existing-model": existing_config}

        at = tab_harness(
            render_edit_model, mock_state, "existing-model", run=False
        )
        at.session_state["editing_existing-model"] = True
        at.run()

        _button_by_label(at, "Save Changes").click()
        at.run()

        assert not at.exception
        assert mock_state.models["existing-model"] is not existing_config
        assert "editing_existing-model" not in at.session_state


class TestEditModelMissingRequiredField:
    """Blank model path is rejected client-side, before ``ConfigStore``."""

    def test_missing_model_path_shows_error_and_skips_config_store(
        self, tab_harness, mock_state, mock_config_store, existing_config
    ):
        mock_state.models["existing-model"] = existing_config
        mock_config_store.load.return_value = {"existing-model": existing_config}

        at = tab_harness(
            render_edit_model, mock_state, "existing-model", run=False
        )
        at.session_state["editing_existing-model"] = True
        at.run()

        _text_input_by_label(at, "Model Path").set_value("")
        _button_by_label(at, "Save Changes").click()
        at.run()

        assert not at.exception
        assert any("required" in e.value for e in at.error)
        mock_config_store.update_model.assert_not_called()
        mock_config_store.add_model.assert_not_called()


class TestEditModelNotYetPersistedUpsertsViaAddModel:
    """Pinned special case (#330 audit item 3): editing a model that is in
    ``state.models`` but *not yet* in the on-disk store (``ConfigStore.load``)
    silently falls back to ``ConfigStore.add_model`` rather than raising or
    calling ``update_model``. Documented, not fixed, by this work item —
    tracked for a future ``ops.update_model`` mint under #292/#152.
    """

    def test_edit_of_unpersisted_model_calls_add_model_not_update_model(
        self, tab_harness, mock_state, mock_config_store, existing_config, tmp_path
    ):
        mock_state.models["existing-model"] = existing_config
        mock_config_store.load.return_value = {}  # not yet persisted

        new_path = tmp_path / "upserted.gguf"
        new_path.touch()

        at = tab_harness(
            render_edit_model, mock_state, "existing-model", run=False
        )
        at.session_state["editing_existing-model"] = True
        at.run()

        _text_input_by_label(at, "Model Path").set_value(str(new_path))
        _button_by_label(at, "Save Changes").click()
        at.run()

        assert not at.exception
        mock_config_store.update_model.assert_not_called()
        mock_config_store.add_model.assert_called_once()
        (upserted_config,), kwargs = mock_config_store.add_model.call_args
        assert upserted_config.name == "existing-model"
        assert kwargs["caller"] == "ui"
        assert any("Saved model 'existing-model'" in s.value for s in at.success)


class TestEditModelCancel:
    """Cancel discards edits without touching ``ConfigStore``."""

    def test_cancel_clears_editing_flag_without_saving(
        self, tab_harness, mock_state, mock_config_store, existing_config
    ):
        mock_state.models["existing-model"] = existing_config

        at = tab_harness(
            render_edit_model, mock_state, "existing-model", run=False
        )
        at.session_state["editing_existing-model"] = True
        at.run()

        _text_input_by_label(at, "Model Path").set_value("/should/not/be/saved.gguf")
        _button_by_label(at, "Cancel").click()
        at.run()

        assert not at.exception
        mock_config_store.add_model.assert_not_called()
        mock_config_store.update_model.assert_not_called()
        assert "editing_existing-model" not in at.session_state
        assert mock_state.models["existing-model"] is existing_config


# ---------------------------------------------------------------------------
# Advanced options — representative subset (not all ~20 fields; #328 scope).
# ---------------------------------------------------------------------------
class TestAddModelAdvancedOptions:
    """A representative slice of the "Advanced Options" expander fields
    reach ``ModelConfig`` unchanged through ``ConfigStore.add_model``.
    """

    def test_mlock_and_temperature_pass_through_to_config(
        self, tab_harness, mock_state, mock_config_store, model_path
    ):
        at = tab_harness(render_add_model, mock_state)

        _text_input_by_label(at, "Model Name").set_value("adv-model")
        _text_input_by_label(at, "Model Path").set_value(model_path)
        for el in at.checkbox:
            if el.label == "Lock Memory in RAM (mlock)":
                el.set_value(True)
        for el in at.number_input:
            if el.label == "Temperature (optional)":
                el.set_value(0.42)
        _button_by_label(at, "Add Model").click()
        at.run()

        assert not at.exception
        mock_config_store.add_model.assert_called_once()
        (added_config,), _ = mock_config_store.add_model.call_args
        assert added_config.mlock is True
        assert added_config.temperature == pytest.approx(0.42)

    def test_extra_args_and_reverse_prompt_pass_through_to_config(
        self, tab_harness, mock_state, mock_config_store, model_path
    ):
        at = tab_harness(render_add_model, mock_state)

        _text_input_by_label(at, "Model Name").set_value("adv-model-2")
        _text_input_by_label(at, "Model Path").set_value(model_path)
        _text_input_by_label(at, "Reverse Prompt (optional)").set_value("### Human:")
        _text_input_by_label(at, "Extra Args (optional)").set_value("--mcp-config /x.json")
        _button_by_label(at, "Add Model").click()
        at.run()

        assert not at.exception
        mock_config_store.add_model.assert_called_once()
        (added_config,), _ = mock_config_store.add_model.call_args
        assert added_config.reverse_prompt == "### Human:"
        assert added_config.extra_args == "--mcp-config /x.json"
