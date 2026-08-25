"""Model Registry tab — validation status overview for all configured models.

Rendered as a table with colour-coded status indicators (ready / missing /
advisory) sourced from ``operations.validate_models()`` (issue #475,
ADR-027) — the same verdict vocabulary the CLI ``model validate`` command,
the ``GET /models/validate`` endpoint, and the ``validate_models`` MCP tool
consume. This tab no longer imports ``core.model_health`` directly or
derives its own status vocabulary (that fork is exactly what ADR-027
closed): it consumes the shared ``ModelValidation``/``ValidationReport``
shape locally (``target == "local"``) and, for a remote node, through
``RemoteNode.get_model_validation()`` / ``RemoteAggregator.get_validation()``
— the sanctioned client layer (thin-client UI rule, ``docs/ARCHITECTURE.md``).

Validation here runs with ``vram=False``: the tab is on the rerun hot path
and the VRAM verdict is advisory, so it never gates a badge (ADR-027 §2/§3).
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st


def render_model_registry(state, registry=None, aggregator=None, target="local"):
    """Render the Model Registry validation table for a single target.

    Args:
        state: The local LauncherState (used only to trigger a refresh
            before a local validate; validation itself reads ConfigStore
            fresh, not ``state.models``).
        registry: NodeRegistry for remote nodes (optional, unused directly
            — validation for a remote target goes through ``aggregator``).
        aggregator: RemoteAggregator for multi-node state (optional).
        target: Selected target node — ``"local"`` or a remote peer name.
    """
    st.subheader("🗂️ Model Registry")

    if target == "local":
        from llauncher import operations as ops

        state.refresh()
        # ``vram=False``: this tab re-renders on every Streamlit widget
        # interaction and the VRAM verdict is advisory-only, so paying an
        # ``nvidia-smi`` shell-out per rerun buys nothing the badge gates on
        # — exactly the per-rerun shell-out economics ADR-027 §2 kept off
        # the hot path. Lockfile staleness (the other advisory) is free.
        report = ops.validate_models(vram=False)
        entries = [m.model_dump(mode="json") for m in report.models]
    else:
        entries = []
        if aggregator is not None:
            remote_report = aggregator.get_validation(target, vram=False)
            if remote_report:
                entries = remote_report.get("models", [])

    if not entries:
        st.info(f"No models configured on **{target}**.")
        return

    rows = [_entry_to_row(target, entry) for entry in entries]

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


def _entry_to_row(target: str, entry: dict) -> dict:
    """Reduce one ``ModelValidation``-shaped dict to a table row.

    Status is derived directly from ``entry["verdicts"]`` — no second copy
    of the ready/missing rule (ADR-027's whole point): a gating verdict
    failure is "missing", an otherwise-``ok`` entry with an advisory
    failure (stale lockfile, insufficient VRAM) surfaces that reason
    without flipping the badge to missing.
    """
    name = entry.get("name", "unknown")
    path = entry.get("model_path", "")
    ok = entry.get("ok", False)
    verdicts = entry.get("verdicts") or []

    if not ok:
        gating_reasons = [v["reason"] for v in verdicts if not v.get("ok") and not v.get("advisory") and v.get("reason")]
        status = f"❌ missing ({'; '.join(gating_reasons)})" if gating_reasons else "❌ missing"
    else:
        advisory_reasons = [v["reason"] for v in verdicts if not v.get("ok") and v.get("advisory") and v.get("reason")]
        status = f"⚠️ ready ({'; '.join(advisory_reasons)})" if advisory_reasons else "✅ ready"

    size_bytes = entry.get("size_bytes")
    size_str = _format_size(size_bytes) if size_bytes is not None else "—"

    return {
        "node": target,
        "name": name,
        "path": path[:80] + "…" if len(path) > 80 else path,
        "size": size_str,
        "last_modified": _format_last_modified(entry.get("last_modified")),
        "status": status,
    }


def _format_last_modified(value) -> str:
    """Render ``last_modified`` (a ``datetime``, an ISO string, or ``None``)."""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            # Defensive: an unparsable string still renders as-is (#347
            # regression posture — never raise on a display-only field).
            return value
    return "—"


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
