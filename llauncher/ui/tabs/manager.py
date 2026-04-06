"""Manager tab - functionality moved to Dashboard."""

import streamlit as st

from llauncher.state import LauncherState


def render_manager(state: LauncherState) -> None:
    """Render the manager view.

    Args:
        state: The launcher state.
    """
    st.header("📁 Model Manager")

    st.info(
        "📌 Model management functionality has been moved to the **Dashboard tab**.\n\n"
        "Use the Dashboard to:\n"
        "- ➕ Add new models\n"
        "- ▶️ Start/⏹️ Stop servers\n"
        "- ✏️ Edit model configurations\n"
        "- 🗑️ Delete models"
    )
