"""Model Registry tab — health status overview for all configured models.

Rendered as a table with colour-coded status indicators (ready / missing /
corrupted / unknown) using the ``check_model_health()`` helper from ADR-005.
"""

from __future__ import annotations

import streamlit as st


def render_model_registry(state, registry=None, aggregator=None, selected_node=None):
    """Render the Model Registry tab.

    Args:
        state: The local LauncherState.
        registry: NodeRegistry for remote nodes (optional).
        aggregator: RemoteAggregator for multi-node state (optional).
        selected_node: Name of selected node or None for all.
    """
    from llauncher.core.model_health import check_model_health

    st.header("🗂️ Model Registry")

    # Gather models to display (reuses the same logic as dashboard.py)
    if registry and aggregator:
        if selected_node and selected_node != "local":
            all_models = {}
            for node_name, node_models in aggregator.get_all_models().items():
                all_models[node_name] = [m.to_dict() if hasattr(m, "to_dict") else m for m in node_models]
        else:
            all_models = aggregator.get_all_models()
            # Merge local models
            state.refresh()
            all_models["local"] = [m.to_dict() for m in state.models.values()]
    else:
        state.refresh()
        all_models = {"local": [m.to_dict() for m in state.models.values()]}

    if not all_models.get("local") and (not all_models or all(v == [] for v in all_models.values())):
        st.info("No models configured. Add one from the Dashboard tab.")
        return

    # ── Collect health data for each model ───────────────────────
    rows = []  # list[dict] for st.dataframe / TableWidget

    for node_name, node_models in all_models.items():
        if selected_node and node_name != selected_node:
            continue

        if not isinstance(node_models, list):
            continue

        for model_data in node_models:
            name = model_data.get("name", "unknown")
            path = model_data.get("model_path", "")
            try:
                health = check_model_health(path)
                dump = health.model_dump()
                valid = dump["valid"]
            except Exception:
                valid, reason = False, "error"
                dump = {"exists": False, "size_bytes": None, "last_modified": None}

            # Status label
            if not dump.get("exists"):
                status = "❌ missing"
            elif valid:
                status = "✅ ready"
            else:
                reason_lower = (dump.get("reason") or "").lower()
                if "too small" in reason_lower or "unreadable" in reason_lower:
                    status = "⚠️ corrupted"
                else:
                    status = f"❓ unknown ({dump.get('reason')})"

            size_str = _format_size(dump.get("size_bytes")) if dump.get("size_bytes") is not None else "—"
            last_mod = (
                dump["last_modified"].strftime("%Y-%m-%d %H:%M")
                if isinstance(dump.get("last_modified"), str) or hasattr(dump.get("last_modified"), "strftime")
                else ("—")
            )

            rows.append({
                "node": node_name,
                "name": name,
                "path": path[:80] + "…" if len(path) > 80 else path,
                "size": size_str,
                "last_modified": last_mod,
                "status": status,
            })

    if not rows:
        st.info("No model entries to display.")
        return

    # ── Render as a Streamlit table (dataframe) ─────────────────
    df = __import__("pandas").DataFrame(rows)
    st.dataframe(
        df,
        column_config={
            "node": "Node",
            "name": st.column_config.TextColumn("Name"),
            "path": st.column_config.TextColumn("Path", width="large"),
            "size": "Size",
            "last_modified": "Modified",
            "status": st.column_config.TextColumn("Status", width="medium"),
        },
        hide_index=True,
    )


def _format_size(nbytes: int) -> str:
    """Human-readable size string."""
    if nbytes < 1024:
        return f"{nbytes} B"
    elif nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.1f} KB"
    elif nbytes < 1024 * 1024 * 1024:
        return f"{nbytes / (1024 ** 2):.1f} MB"
    else:
        return f"{nbytes / (1024 ** 3):.2f} GB"
