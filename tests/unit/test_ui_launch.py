"""Unit tests for the ``llauncher-ui`` console-script entry point."""

from pathlib import Path

from llauncher.ui import launch


def test_resolve_ui_host_defaults_to_loopback():
    # No override -> loopback, per the no-auth-dashboard default (C12).
    assert launch.resolve_ui_host({}) == "127.0.0.1"


def test_resolve_ui_host_honors_env_override():
    assert launch.resolve_ui_host({"LLAUNCHER_UI_HOST": "0.0.0.0"}) == "0.0.0.0"


def test_resolve_ui_host_blank_override_falls_back_to_loopback():
    # A blank export must not silently publish the dashboard.
    assert launch.resolve_ui_host({"LLAUNCHER_UI_HOST": ""}) == "127.0.0.1"


def test_build_streamlit_argv_runs_the_packaged_app():
    app = Path("/opt/llauncher/ui/app.py")
    argv = launch.build_streamlit_argv(app, "127.0.0.1")

    assert argv[1:4] == ["-m", "streamlit", "run"]
    assert str(app) in argv


def test_build_streamlit_argv_binds_resolved_host():
    argv = launch.build_streamlit_argv(Path("/x/app.py"), "192.168.1.5")
    assert argv[argv.index("--server.address") + 1] == "192.168.1.5"
