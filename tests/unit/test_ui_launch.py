"""Unit tests for the ``llauncher-ui`` console-script entry point."""

from pathlib import Path

import pytest

from llauncher.ui import launch


def test_resolve_ui_host_defaults_to_loopback():
    # No override -> loopback, per the no-auth-dashboard default (C12).
    assert launch.resolve_ui_host({}) == "127.0.0.1"


def test_resolve_ui_host_honors_env_override():
    assert launch.resolve_ui_host({"LLAUNCHER_UI_HOST": "0.0.0.0"}) == "0.0.0.0"


def test_resolve_ui_host_blank_override_falls_back_to_loopback():
    # A blank export must not silently publish the dashboard.
    assert launch.resolve_ui_host({"LLAUNCHER_UI_HOST": ""}) == "127.0.0.1"


def test_resolve_ui_port_defaults_to_8501():
    # No override -> Streamlit's own default (#356). Absence is not garbage.
    assert launch.resolve_ui_port({}) == 8501


def test_resolve_ui_port_blank_override_falls_back_to_default():
    assert launch.resolve_ui_port({"LLAUNCHER_UI_PORT": ""}) == 8501


def test_resolve_ui_port_honors_env_override():
    assert launch.resolve_ui_port({"LLAUNCHER_UI_PORT": "9999"}) == 9999


@pytest.mark.parametrize("bad_value", ["not-a-number", "8080.5", "8080x"])
def test_resolve_ui_port_non_integer_fails_loud(bad_value):
    # PARSE-AT-THE-DOOR: garbage is not absence, so it must not silently
    # fall back to the default.
    with pytest.raises(ValueError, match="LLAUNCHER_UI_PORT"):
        launch.resolve_ui_port({"LLAUNCHER_UI_PORT": bad_value})


@pytest.mark.parametrize("bad_value", ["0", "-1", "65536", "100000"])
def test_resolve_ui_port_out_of_range_fails_loud(bad_value):
    with pytest.raises(ValueError, match="LLAUNCHER_UI_PORT"):
        launch.resolve_ui_port({"LLAUNCHER_UI_PORT": bad_value})


def test_build_streamlit_argv_runs_the_packaged_app():
    app = Path("/opt/llauncher/ui/app.py")
    argv = launch.build_streamlit_argv(app, "127.0.0.1", 8501)

    assert argv[1:4] == ["-m", "streamlit", "run"]
    assert str(app) in argv


def test_build_streamlit_argv_binds_resolved_host():
    argv = launch.build_streamlit_argv(Path("/x/app.py"), "192.168.1.5", 8501)
    assert argv[argv.index("--server.address") + 1] == "192.168.1.5"


def test_build_streamlit_argv_binds_resolved_port():
    argv = launch.build_streamlit_argv(Path("/x/app.py"), "127.0.0.1", 9999)
    assert argv[argv.index("--server.port") + 1] == "9999"


def test_build_streamlit_argv_includes_both_address_and_port_flags():
    argv = launch.build_streamlit_argv(Path("/x/app.py"), "127.0.0.1", 8501)
    assert "--server.address" in argv
    assert "--server.port" in argv
