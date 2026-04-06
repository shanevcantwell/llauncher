"""Streamlit UI for llauncher."""

import streamlit as st

from llauncher.state import LauncherState


# Configure page
st.set_page_config(
    page_title="llauncher",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_state() -> LauncherState:
    """Get or create the launcher state (cached across reruns)."""
    return LauncherState()


def main():
    """Main entry point for the Streamlit app."""
    st.title("🚀 llauncher")
    st.markdown("Manage your llama.cpp servers")

    # Get state
    state = get_state()

    # Sidebar navigation
    st.sidebar.header("Navigation")
    page = st.sidebar.radio(
        "Page navigation",
        ["Dashboard", "Manager", "Running"],
        index=0,
        label_visibility="collapsed",
    )

    # Refresh button
    if st.sidebar.button("🔄 Refresh", use_container_width=True):
        state.refresh()
        st.rerun()

    # Render selected page
    if page == "Dashboard":
        from llauncher.ui.tabs.dashboard import render_dashboard

        render_dashboard(state)

    elif page == "Manager":
        from llauncher.ui.tabs.manager import render_manager

        render_manager(state)

    elif page == "Running":
        from llauncher.ui.tabs.running import render_running

        render_running(state)


if __name__ == "__main__":
    main()
