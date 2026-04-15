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

        col1, col2, col3 = st.columns(3)
        with col1:
            default_port = st.number_input(
                "Default Port (optional)",
                min_value=1024,
                max_value=65535,
                value=8080,
                help="Leave as 0 for auto-allocation",
            )
        with col2:
            n_gpu_layers = st.number_input(
                "GPU Layers", min_value=0, max_value=1024, value=255
            )
        with col3:
            ctx_size = st.number_input(
                "Context Size", min_value=1024, value=131072
            )

        col4, col5 = st.columns(2)
        with col4:
            threads = st.number_input("Threads (optional)", min_value=0, value=0)
        with col5:
            flash_attn = st.selectbox("Flash Attention", ["on", "off", "auto"], index=0)

        no_mmap = st.checkbox("Disable Memory Mapping (no-mmap)", value=False)

        # Additional options (expandable)
        with st.expander("Advanced Options", expanded=False):
            col_adv1, col_adv2 = st.columns(2)
            with col_adv1:
                parallel = st.number_input(
                    "Parallel Slots (-np)", min_value=1, value=1
                )
            with col_adv2:
                mlock = st.checkbox("Lock Memory in RAM (mlock)", value=False)

            col_adv3, col_adv4, col_adv5 = st.columns(3)
            with col_adv3:
                n_cpu_moe = st.number_input(
                    "CPU MOE Threads (-ncmoe, optional)", min_value=0, value=0
                )
            with col_adv4:
                batch_size = st.number_input(
                    "Batch Size (optional)", min_value=0, value=0
                )
            with col_adv5:
                temperature = st.number_input(
                    "Temperature (optional)", min_value=0.0, value=0.7, step=0.1
                )

            col_adv6, col_adv7, col_adv8 = st.columns(3)
            with col_adv6:
                top_k = st.number_input(
                    "Top-K (optional)", min_value=0, value=40
                )
            with col_adv7:
                top_p = st.number_input(
                    "Top-P (optional)",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.9,
                    step=0.01,
                )
            with col_adv8:
                min_p = st.number_input(
                    "Min-P (optional)",
                    min_value=0.0,
                    max_value=1.0,
                    value=0.1,
                    step=0.01,
                )

            reverse_prompt = st.text_input(
                "Reverse Prompt (optional)",
                help="Halt generation when this string is encountered",
            )

            extra_args = st.text_input(
                "Extra Args (optional)",
                help="Additional command-line arguments (e.g., '--mcp-config /path/to/file.json')",
            )

        submitted = st.form_submit_button("Add Model", use_container_width=True)

        if submitted:
            _process_add_model(state, name, model_path, mmproj_path, default_port,
                             n_gpu_layers, ctx_size, threads, flash_attn,
                             no_mmap, parallel, mlock, n_cpu_moe, batch_size,
                             temperature, top_k, top_p, min_p, reverse_prompt, extra_args)


def _process_add_model(
    state: LauncherState,
    name: str,
    model_path: str,
    mmproj_path: str | None,
    default_port: int,
    n_gpu_layers: int,
    ctx_size: int,
    threads: int,
    flash_attn: str,
    no_mmap: bool,
    parallel: int,
    mlock: bool,
    n_cpu_moe: int,
    batch_size: int,
    temperature: float,
    top_k: int,
    top_p: float,
    min_p: float,
    reverse_prompt: str,
    extra_args: str,
) -> None:
    """Process the add model form submission.

    Args:
        state: The launcher state.
        name: Model name.
        model_path: Path to GGUF file.
        mmproj_path: Path to multimodal projector (optional).
        default_port: Default port for the server.
        n_gpu_layers: Number of GPU layers.
        ctx_size: Context size.
        threads: Number of threads.
        flash_attn: Flash attention setting.
        no_mmap: Disable memory mapping flag.
        parallel: Parallel slots.
        mlock: Lock memory in RAM flag.
        n_cpu_moe: CPU MOE threads.
        batch_size: Batch size.
        temperature: Temperature value.
        top_k: Top-K value.
        top_p: Top-P value.
        min_p: Min-P value.
        reverse_prompt: Reverse prompt string.
        extra_args: Additional command-line arguments.
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

        default_port_val = default_port if default_port >= 1024 else None

        config = ModelConfig(
            name=name,
            model_path=model_path,
            mmproj_path=mmproj_path,
            default_port=default_port_val,
            n_gpu_layers=n_gpu_layers,
            ctx_size=ctx_size,
            threads=threads if threads > 0 else None,
            flash_attn=flash_attn,
            no_mmap=no_mmap,
            parallel=parallel,
            mlock=mlock,
            n_cpu_moe=n_cpu_moe if n_cpu_moe > 0 else None,
            batch_size=batch_size if batch_size > 0 else None,
            temperature=temperature if temperature > 0 else None,
            top_k=top_k if top_k > 0 else None,
            top_p=top_p if top_p > 0 else None,
            min_p=min_p if min_p > 0 else None,
            reverse_prompt=reverse_prompt.strip() if reverse_prompt else None,
            extra_args=extra_args.strip() if extra_args else "",
        )

        ConfigStore.add_model(config)
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

        col1, col2, col3 = st.columns(3)
        with col1:
            default_port = st.number_input(
                "Default Port (optional)",
                min_value=1024,
                max_value=65535,
                value=config.default_port or 8080,
            )
        with col2:
            n_gpu_layers = st.number_input(
                "GPU Layers", min_value=0, max_value=1024, value=config.n_gpu_layers
            )
        with col3:
            ctx_size = st.number_input(
                "Context Size", min_value=1024, value=config.ctx_size
            )

        col4, col5 = st.columns(2)
        with col4:
            threads = st.number_input(
                "Threads (optional)", min_value=0, value=config.threads or 0
            )
        with col5:
            flash_idx = ["on", "off", "auto"].index(config.flash_attn)
            flash_attn = st.selectbox(
                "Flash Attention", ["on", "off", "auto"], index=flash_idx
            )

        no_mmap = st.checkbox("Disable Memory Mapping (no-mmap)", value=config.no_mmap)

        with st.expander("Advanced Options", expanded=False):
            col_adv1, col_adv2 = st.columns(2)
            with col_adv1:
                parallel = st.number_input(
                    "Parallel Slots (-np)", min_value=1, value=config.parallel
                )
            with col_adv2:
                mlock = st.checkbox("Lock Memory in RAM (mlock)", value=config.mlock)

            col_adv3, col_adv4, col_adv5 = st.columns(3)
            with col_adv3:
                n_cpu_moe = st.number_input(
                    "CPU MOE Threads", min_value=0, value=config.n_cpu_moe or 0
                )
            with col_adv4:
                batch_size = st.number_input(
                    "Batch Size", min_value=0, value=config.batch_size or 0
                )
            with col_adv5:
                temperature = st.number_input(
                    "Temperature",
                    min_value=0.0,
                    value=config.temperature or 0.7,
                    step=0.1,
                )

            col_adv6, col_adv7, col_adv8 = st.columns(3)
            with col_adv6:
                top_k = st.number_input("Top-K", min_value=0, value=config.top_k or 40)
            with col_adv7:
                top_p = st.number_input(
                    "Top-P",
                    min_value=0.0,
                    max_value=1.0,
                    value=config.top_p or 0.9,
                    step=0.01,
                )
            with col_adv8:
                min_p = st.number_input(
                    "Min-P",
                    min_value=0.0,
                    max_value=1.0,
                    value=config.min_p or 0.1,
                    step=0.01,
                )

            reverse_prompt = st.text_input(
                "Reverse Prompt", value=config.reverse_prompt or ""
            )

            extra_args = st.text_input(
                "Extra Args",
                value=config.extra_args or "",
                help="Additional command-line arguments",
            )

        col_submit, col_cancel = st.columns(2)
        with col_submit:
            submitted = st.form_submit_button("Save Changes", use_container_width=True)
        with col_cancel:
            cancel_clicked = st.form_submit_button("Cancel", use_container_width=True)

        if cancel_clicked:
            del st.session_state[f"editing_{model_name}"]
            st.rerun()

        if submitted:
            _process_edit_model(state, model_name, model_path, mmproj_path, default_port,
                              n_gpu_layers, ctx_size, threads, flash_attn, no_mmap,
                              parallel, mlock, n_cpu_moe, batch_size, temperature,
                              top_k, top_p, min_p, reverse_prompt, extra_args)


def _process_edit_model(
    state: LauncherState,
    model_name: str,
    model_path: str,
    mmproj_path: str,
    default_port: int,
    n_gpu_layers: int,
    ctx_size: int,
    threads: int,
    flash_attn: str,
    no_mmap: bool,
    parallel: int,
    mlock: bool,
    n_cpu_moe: int,
    batch_size: int,
    temperature: float,
    top_k: int,
    top_p: float,
    min_p: float,
    reverse_prompt: str,
    extra_args: str,
) -> None:
    """Process the edit model form submission.

    Args:
        state: The launcher state.
        model_name: Name of the model being edited.
        model_path: Path to GGUF file.
        mmproj_path: Path to multimodal projector.
        default_port: Default port.
        n_gpu_layers: Number of GPU layers.
        ctx_size: Context size.
        threads: Number of threads.
        flash_attn: Flash attention setting.
        no_mmap: Disable memory mapping flag.
        parallel: Parallel slots.
        mlock: Lock memory in RAM flag.
        n_cpu_moe: CPU MOE threads.
        batch_size: Batch size.
        temperature: Temperature value.
        top_k: Top-K value.
        top_p: Top-P value.
        min_p: Min-P value.
        reverse_prompt: Reverse prompt string.
        extra_args: Additional command-line arguments.
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
                "default_port": default_port if default_port >= 1024 else None,
                "n_gpu_layers": n_gpu_layers,
                "ctx_size": ctx_size,
                "threads": threads if threads > 0 else None,
                "flash_attn": flash_attn,
                "no_mmap": no_mmap,
                "parallel": parallel,
                "mlock": mlock,
                "n_cpu_moe": n_cpu_moe if n_cpu_moe > 0 else None,
                "batch_size": batch_size if batch_size > 0 else None,
                "temperature": temperature if temperature > 0 else None,
                "top_k": top_k if top_k > 0 else None,
                "top_p": top_p if top_p > 0 else None,
                "min_p": min_p if min_p > 0 else None,
                "reverse_prompt": reverse_prompt or None,
                "extra_args": extra_args or "",
            }
        )

        persisted_models = ConfigStore.load()
        if model_name in persisted_models:
            ConfigStore.update_model(model_name, updated_config)
            st.success(f"Updated model '{model_name}'")
        else:
            ConfigStore.add_model(updated_config)
            st.success(f"Saved model '{model_name}'")

        state.models[model_name] = updated_config
        del st.session_state[f"editing_{model_name}"]
        st.rerun()

    except Exception as e:
        st.error(f"Error saving model: {e}")
