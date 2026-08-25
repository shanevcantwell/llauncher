"""Form rendering for dashboard tab (add/edit models)."""

import streamlit as st

from llauncher.state import LauncherState
from llauncher.core.config import ConfigStore


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

            # ADR-026 / issue #477: extra_args is a verbatim llama-server
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

    with st.form("edit_model_form", clear_on_submit=True):
        st.text_input("Model Name", value=model_name, disabled=True)

        st.markdown("**Model Path**")
        model_path = st.text_input(
            "Model Path", value=config.model_path, help="Path to the GGUF file"
        )
        mmproj_path = st.text_input(
            "MMProj Path (optional)",
            value=config.mmproj_path or "",
            help="Path to multimodal projector",
        )

        # Per ADR-LLNCH-010, port is no longer a model attribute — supplied at start time.
        col1, col2 = st.columns(2)
        with col1:
            n_gpu_layers = st.number_input(
                "GPU Layers", min_value=0, max_value=1024, value=config.n_gpu_layers
            )
        with col2:
            ctx_size = st.number_input(
                "Context Size", min_value=1024, value=config.ctx_size
            )

        with st.expander("Advanced Options", expanded=False):
            col_adv1, col_adv2 = st.columns(2)
            with col_adv1:
                parallel = st.number_input(
                    "Parallel Slots (-np)", min_value=1, value=config.parallel
                )
            with col_adv2:
                metrics = st.checkbox(
                    "Enable Prometheus Metrics (--metrics)",
                    value=config.metrics,
                    help="Exposes the /metrics endpoint for tps, kv-cache, and draft-acceptance telemetry.",
                )

            slots = st.checkbox(
                "Expose /slots monitoring endpoint (--slots)",
                value=config.slots,
                help="Includes per-slot prompt text — sensitive, default off.",
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
            )

        col_submit, col_cancel = st.columns(2)
        with col_submit:
            submitted = st.form_submit_button("Save Changes", width='stretch')
        with col_cancel:
            cancel_clicked = st.form_submit_button("Cancel", width='stretch')

        if cancel_clicked:
            del st.session_state[f"editing_{model_name}"]
            st.rerun()

        if submitted:
            _process_edit_model(
                state, model_name, model_path, mmproj_path,
                n_gpu_layers, ctx_size, parallel, metrics, slots, extra_args,
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
    if not model_path:
        st.error("Model path is required")
        return

    try:
        from llauncher.models.config import ModelConfig

        config = state.models.get(model_name)
        if not config:
            st.error(f"Model '{model_name}' not found")
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
            st.success(f"Updated model '{model_name}'")
        else:
            ConfigStore.add_model(updated_config, caller="ui")
            st.success(f"Saved model '{model_name}'")

        state.models[model_name] = updated_config
        del st.session_state[f"editing_{model_name}"]
        st.rerun()

    except Exception as e:
        st.error(f"Error saving model: {e}")
