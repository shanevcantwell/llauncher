"""Running tab - functionality moved to Dashboard."""

import streamlit as st

from llauncher.state import LauncherState


def render_running(state: LauncherState) -> None:
    """Render the running servers view.

    Args:
        state: The launcher state.
    """
    st.header("🏃 Running Servers")

    st.info(
        "📌 Running server view has been consolidated into the **Dashboard tab**.\n\n"
        "In the Dashboard, you can now:\n"
        "- View all models (running and stopped) in one place\n"
        "- See live logs (last 200 lines) for running servers\n"
        "- Logs auto-refresh every 3 seconds\n"
        "- Start/Stop servers directly from model cards"
    )
