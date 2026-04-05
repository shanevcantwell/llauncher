"""Running tab showing active servers and logs."""

import streamlit as st

from llauncher.state import LauncherState
from llauncher.core.process import stream_logs


def render_running(state: LauncherState) -> None:
    """Render the running servers view.

    Args:
        state: The launcher state.
    """
    st.header("🏃 Running Servers")

    if not state.running:
        st.info("No servers currently running.")
        return

    # Refresh button
    if st.button("🔄 Refresh Server List", use_container_width=True):
        state.refresh_running_servers()
        st.rerun()

    # Table of running servers
    st.subheader(f"Active Servers ({len(state.running)})")

    for port, server in state.running.items():
        with st.expander(
            f"**Port {port}** - {server.config_name} (PID: {server.pid})",
            expanded=False,
        ):
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(f"**Model**")
                st.markdown(f"**Port**")
                st.markdown(f"**PID**")
            with col2:
                st.markdown(f"`{server.config_name}`")
                st.markdown(f"`{server.port}`")
                st.markdown(f"`{server.pid}`")
            with col3:
                # Actions
                if st.button(
                    "⏹️ Stop",
                    use_container_width=True,
                    key=f"stop_{port}",
                ):
                    success, message = state.stop_server(port, caller="ui")
                    if success:
                        st.success(message)
                    else:
                        st.error(message)
                    st.rerun()

            st.divider()

            # Logs viewer
            st.markdown("**Logs**")

            log_lines = stream_logs(server.pid, lines=200)

            if log_lines:
                # Auto-scroll to bottom
                log_container = st.container()
                with log_container:
                    st.code(
                        "\n".join(log_lines),
                        language="bash",
                        height=300,
                    )
            else:
                st.info("No logs available")

            st.divider()


def render_log_viewer(server, lines: int = 200) -> None:
    """Render a log viewer for a specific server.

    Args:
        server: RunningServer object.
        lines: Number of log lines to show.
    """
    log_lines = stream_logs(server.pid, lines=lines)

    if log_lines:
        st.code("\n".join(log_lines), language="bash", height=400)
    else:
        st.info("No logs available")
