"""Form rendering for dashboard tab (add/edit models)."""

import streamlit as st

from llauncher.state import LauncherState
from llauncher.core.config import ConfigStore
from llauncher.ui.tabs.model_card import edit_saved_toast_key


def _edit_error_key(model_name: str) -> str:
    """Session-state key for a sticky edit-save failure message (#494).

    Mirrors ``model_card.py``'s ``_start_error_key``/``#401`` idiom: the
    Save button's ``on_click`` callback (``_save_edit_callback`` /
    ``_process_edit_model`` below) runs in Streamlit's pre-script callback
    context, where ``st.error()`` calls are silently dropped — nothing has
    started rendering yet this run. A failure persists the message here
    instead and leaves ``editing_{model_name}`` set, so ``render_edit_model``
    stays in edit mode on the next (normal, non-callback) render and can
    display it via :func:`_render_edit_error`.
    """
    return f"edit_error_{model_name}"


def _render_edit_error(model_name: str) -> None:
    """Render the sticky edit-save failure message left by
    :func:`_process_edit_model`, if any. See :func:`_edit_error_key`.
    """
    message = st.session_state.get(_edit_error_key(model_name))
    if message:
        st.error(message)


def render_add_model(state: LauncherState) -> None:
    """Render the form to add a new model.

    Args:
        state: The launcher state.
    """
    with st.form("add_model_form", clear_on_submit=True):
        name = st.text_input("Model Name", help="Unique identifier for this model")
        st.markdown("**Model Path**")
        st.caption(
            "Common locations: ~/.cache/llama.cpp/, ~/models/, /usr/share/llama.cpp/"
        )
        model_path = st.text_input(
            "Model Path", help="Path to the GGUF file (e.g., /path/to/model.gguf)"
        )
        mmproj_path = st.text_input(
            "MMProj Path (optional)",
            help="Path to multimodal projector for vision models",
        )

        # Per ADR-LLNCH-010, port is no longer a model attribute — supplied at start time.
        col1, col2 = st.columns(2)
        with col1:
            n_gpu_layers = st.number_input(
                "GPU Layers", min_value=0, max_value=1024, value=255
            )
        with col2:
            ctx_size = st.number_input(
                "Context Size", min_value=1024, value=131072
            )

        # Additional options (expandable)
        with st.expander("Advanced Options", expanded=False):
            col_adv1, col_adv2 = st.columns(2)
            with col_adv1:
                parallel = st.number_input(
                    "Parallel Slots (-np)", min_value=1, value=1
                )
            with col_adv2:
                metrics = st.checkbox(
                    "Enable Prometheus Metrics (--metrics)",
                    value=True,
                    help="Exposes the /metrics endpoint for tps, kv-cache, and draft-acceptance telemetry.",
                )

            slots = st.checkbox(
                "Expose /slots monitoring endpoint (--slots)",
                value=False,
                help="Includes per-slot prompt text — sensitive, default off.",
            )

            # ADR-LLNCH-026 / issue #477: extra_args is a verbatim llama-server
            # flag passthrough — no dedicated widget per flag, no pydantic
            # content validation. Denied (llauncher-owned) flags are
            # rejected at launch time, not here.
            extra_args = st.text_area(
                "Extra Args",
                help=(
                    "Additional llama-server command-line flags, in the "
                    "spelling from `llama-server --help` (e.g. "
                    "'--flash-attn on --cache-type-k q4_0 --threads 8'). "
                    "Flags llauncher owns (--alias, -m/--model, "
                    "--host/--port, --api-key, --metrics, --slots/"
                    "--no-slots) are rejected at launch time."
                ),
            )

        submitted = st.form_submit_button("Add Model", width='stretch')

        if submitted:
            _process_add_model(
                state, name, model_path, mmproj_path,
                n_gpu_layers, ctx_size, parallel, metrics, slots, extra_args,
            )


def _process_add_model(
    state: LauncherState,
    name: str,
    model_path: str,
    mmproj_path: str | None,
    n_gpu_layers: int,
    ctx_size: int,
    parallel: int,
    metrics: bool,
    slots: bool,
    extra_args: str,
) -> None:
    """Process the add model form submission.

    Args:
        state: The launcher state.
        name: Model name.
        model_path: Path to GGUF file.
        mmproj_path: Path to multimodal projector (optional).
        n_gpu_layers: Number of GPU layers.
        ctx_size: Context size.
        parallel: Parallel slots.
        metrics: Enable Prometheus /metrics endpoint flag.
        slots: Enable /slots monitoring endpoint flag.
        extra_args: Verbatim llama-server flag passthrough.
    """
    # Strip whitespace from inputs
    name = name.strip()
    model_path = model_path.strip()
    mmproj_path = mmproj_path.strip() if mmproj_path else None

    if not name or not model_path:
        st.error("Model name and path are required")
        return

    if name in state.models:
        st.error(f"Model '{name}' already exists")
        return

    try:
        from llauncher.models.config import ModelConfig

        config = ModelConfig(
            name=name,
            model_path=model_path,
            mmproj_path=mmproj_path,
            n_gpu_layers=n_gpu_layers,
            ctx_size=ctx_size,
            parallel=parallel,
            metrics=metrics,
            slots=slots,
            extra_args=extra_args.strip() if extra_args else "",
        )

        ConfigStore.add_model(config, caller="ui")
        state.models[name] = config
        st.success(f"Added model '{name}'")
        st.rerun()

    except Exception as e:
        st.error(f"Error adding model: {e}")


def render_edit_model(state: LauncherState, model_name: str | None = None) -> None:
    """Render the form to edit an existing model.

    Args:
        state: The launcher state.
        model_name: Name of the model to edit.
    """
    if model_name is None:
        for name in state.models:
            if st.session_state.get(f"editing_{name}"):
                model_name = name
                break

    if not model_name:
        return

    config = state.models.get(model_name)
    if not config:
        st.error(f"Model '{model_name}' not found")
        return

    st.subheader(f"✏️ Edit Model: {model_name}")
    _render_edit_error(model_name)

    with st.form("edit_model_form", clear_on_submit=True):
        st.text_input("Model Name", value=model_name, disabled=True)

        st.markdown("**Model Path**")
        model_path = st.text_input(
            "Model Path",
            value=config.model_path,
            help="Path to the GGUF file",
            key="edit_model_path",
        )
        mmproj_path = st.text_input(
            "MMProj Path (optional)",
            value=config.mmproj_path or "",
            help="Path to multimodal projector",
            key="edit_mmproj_path",
        )

        # Per ADR-LLNCH-010, port is no longer a model attribute — supplied at start time.
        col1, col2 = st.columns(2)
        with col1:
            n_gpu_layers = st.number_input(
                "GPU Layers", min_value=0, max_value=1024, value=config.n_gpu_layers,
                key="edit_n_gpu_layers",
            )
        with col2:
            ctx_size = st.number_input(
                "Context Size", min_value=1024, value=config.ctx_size,
                key="edit_ctx_size",
            )

        with st.expander("Advanced Options", expanded=False):
            col_adv1, col_adv2 = st.columns(2)
            with col_adv1:
                parallel = st.number_input(
                    "Parallel Slots (-np)", min_value=1, value=config.parallel,
                    key="edit_parallel",
                )
            with col_adv2:
                metrics = st.checkbox(
                    "Enable Prometheus Metrics (--metrics)",
                    value=config.metrics,
                    help="Exposes the /metrics endpoint for tps, kv-cache, and draft-acceptance telemetry.",
                    key="edit_metrics",
                )

            slots = st.checkbox(
                "Expose /slots monitoring endpoint (--slots)",
                value=config.slots,
                help="Includes per-slot prompt text — sensitive, default off.",
                key="edit_slots",
            )

            extra_args = st.text_area(
                "Extra Args",
                value=config.extra_args or "",
                help=(
                    "Additional llama-server command-line flags, in the "
                    "spelling from `llama-server --help`. Flags llauncher "
                    "owns (--alias, -m/--model, --host/--port, --api-key, "
                    "--metrics, --slots/--no-slots) are rejected at launch "
                    "time."
                ),
                key="edit_extra_args",
            )

        col_submit, col_cancel = st.columns(2)
        with col_submit:
            # on_click (#494): runs before the script body, so a
            # successful save's editing_{name} clear (inside
            # _process_edit_model) is already in effect by the time
            # models.py's routing check runs later this same pass — no
            # st.rerun() needed, and no second state.refresh() /
            # process-table walk. The submitted widget values are read
            # from st.session_state by key inside the callback (Streamlit
            # applies a form's pending widget updates before invoking its
            # submit button's on_click), not passed positionally, since
            # the plain local variables above reflect this run's *prior*
            # values, not the just-submitted ones.
            st.form_submit_button(
                "Save Changes", width='stretch',
                on_click=_save_edit_callback, args=(state, model_name),
            )
        with col_cancel:
            # Same on_click shape as Save, for the same reason.
            st.form_submit_button(
                "Cancel", width='stretch',
                on_click=_cancel_edit_callback, args=(model_name,),
            )


def _cancel_edit_callback(model_name: str) -> None:
    """``on_click`` callback for the edit form's Cancel button (#494).

    Session-state mutation only (ADR-LLNCH-025 view state) — no ``st``
    render calls, so it is correct in the pre-script callback context.
    """
    st.session_state.pop(f"editing_{model_name}", None)


def _save_edit_callback(state: LauncherState, model_name: str) -> None:
    """``on_click`` callback for the edit form's Save Changes button (#494).

    Reads the just-submitted widget values from ``st.session_state`` by
    key — Streamlit applies a form's pending widget updates before
    invoking the submit button's callback, so these reflect the values the
    operator entered, not the previous run's. Delegates to
    :func:`_process_edit_model` for the actual validate/persist logic,
    unchanged from before this fix except that it no longer calls
    ``st.error``/``st.success``/``st.rerun`` directly (callback-unsafe —
    see :func:`_process_edit_model`'s docstring).
    """
    _process_edit_model(
        state,
        model_name,
        st.session_state.get("edit_model_path", ""),
        st.session_state.get("edit_mmproj_path", ""),
        st.session_state.get("edit_n_gpu_layers"),
        st.session_state.get("edit_ctx_size"),
        st.session_state.get("edit_parallel"),
        st.session_state.get("edit_metrics"),
        st.session_state.get("edit_slots"),
        st.session_state.get("edit_extra_args", ""),
    )


def _process_edit_model(
    state: LauncherState,
    model_name: str,
    model_path: str,
    mmproj_path: str,
    n_gpu_layers: int,
    ctx_size: int,
    parallel: int,
    metrics: bool,
    slots: bool,
    extra_args: str,
) -> None:
    """Process the edit model form submission.

    Callback-safe (#494): called from the Save button's ``on_click``
    (``_save_edit_callback``), which runs *before* the script body — any
    ``st.error()``/``st.success()``/``st.rerun()`` call made here would be
    silently dropped, since nothing has started rendering yet this run.
    Feedback is persisted to ``st.session_state`` instead (ADR-LLNCH-025
    view state, not cached lifecycle truth — the write-through-then-mirror
    idiom already used elsewhere in this UI, e.g.
    ``model_card.py``'s ``_start_error_key``/#401):

    - **Failure** leaves ``editing_{model_name}`` set and writes
      :func:`_edit_error_key`'s message, so ``render_edit_model`` stays on
      the edit form on its next (normal, non-callback) render and shows
      the sticky error via :func:`_render_edit_error` — it survives, it
      doesn't vanish like a toast would.
    - **Success** clears the ``editing_{model_name}`` flag — routing back
      to the card grid within this *same* script run, no ``st.rerun()``
      needed — and writes :func:`llauncher.ui.tabs.model_card.edit_saved_toast_key`'s
      message, since the edit form itself won't render again this run to
      show anything; ``model_card.py``'s ``_render_edit_saved_toast``
      shows it once from the card grid instead.

    Args:
        state: The launcher state.
        model_name: Name of the model being edited.
        model_path: Path to GGUF file.
        mmproj_path: Path to multimodal projector.
        n_gpu_layers: Number of GPU layers.
        ctx_size: Context size.
        parallel: Parallel slots.
        metrics: Enable Prometheus /metrics endpoint flag.
        slots: Enable /slots monitoring endpoint flag.
        extra_args: Verbatim llama-server flag passthrough.
    """
    error_key = _edit_error_key(model_name)

    if not model_path:
        st.session_state[error_key] = "Model path is required"
        return

    try:
        config = state.models.get(model_name)
        if not config:
            st.session_state[error_key] = f"Model '{model_name}' not found"
            return

        updated_config = config.model_copy(
            update={
                "model_path": model_path,
                "mmproj_path": mmproj_path or None,
                "n_gpu_layers": n_gpu_layers,
                "ctx_size": ctx_size,
                "parallel": parallel,
                "metrics": metrics,
                "slots": slots,
                "extra_args": extra_args or "",
            }
        )

        persisted_models = ConfigStore.load()
        if model_name in persisted_models:
            ConfigStore.update_model(model_name, updated_config, caller="ui")
        else:
            ConfigStore.add_model(updated_config, caller="ui")

        state.models[model_name] = updated_config
        st.session_state[error_key] = None
        st.session_state[edit_saved_toast_key(model_name)] = (
            f"Saved config for {model_name}"
        )
        st.session_state.pop(f"editing_{model_name}", None)

    except Exception as e:
        st.session_state[error_key] = f"Error saving model: {e}"
