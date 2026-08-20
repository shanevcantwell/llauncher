"""Model Registry tab — health status overview for all configured models.

Rendered as a table with colour-coded status indicators (ready / missing /
corrupted / unknown) using the ``check_model_health()`` helper from ADR-005.
"""

from __future__ import annotations

import streamlit as st


def render_model_registry(state, registry=None, aggregator=None, target="local"):
    """Render the Model Registry health table for a single target.

    Stage 2 of M4 Slice 13 (#50) folded the registry view into the
    consolidated Models tab and dropped the legacy "All Nodes" branch.
    The function now scopes to a single ``target`` (string), matching
    the rest of the M4 tab API.

    Args:
        state: The local LauncherState.
        registry: NodeRegistry for remote nodes (optional).
        aggregator: RemoteAggregator for multi-node state (optional).
        target: Selected target node — ``"local"`` or a remote peer name.
    """
    from llauncher.core.model_health import check_model_health

    st.subheader("🗂️ Model Registry")

    # Gather models for the target node only.
    if target == "local":
        state.refresh()
        node_models = [m.to_dict() for m in state.models.values()]
    else:
        if aggregator:
            raw = aggregator.get_all_models().get(target, [])
            node_models = [m.to_dict() if hasattr(m, "to_dict") else m for m in raw]
        else:
            node_models = []

    if not node_models:
        st.info(f"No models configured on **{target}**.")
        return

    # ── Collect health data for each model ───────────────────────
    rows = []  # list[dict] for st.dataframe / TableWidget

    for model_data in node_models:
        name = model_data.get("name", "unknown")
        path = model_data.get("model_path", "")
        try:
            health = check_model_health(path)
            dump = health.model_dump()
            valid = dump["valid"]
        except Exception:
            valid = False
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
        raw_last_modified = dump.get("last_modified")
        if hasattr(raw_last_modified, "strftime"):
            last_mod = raw_last_modified.strftime("%Y-%m-%d %H:%M")
        elif isinstance(raw_last_modified, str):
            # Defensive: a str-typed timestamp (e.g. from a pre-serialized
            # payload) is displayed as-is rather than crashing on .strftime.
            last_mod = raw_last_modified
        else:
            last_mod = "—"

        rows.append({
            "node": target,
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
