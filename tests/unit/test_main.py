"""Tests for llauncher CLI entry point (__main__.py)."""

import pytest
import sys
from io import StringIO
from unittest.mock import patch, MagicMock

from llauncher.models.config import ModelConfig


class TestNoCommand:
    """Test missing command shows argparse error."""

    def test_no_command_shows_help(self):
        """Missing command argument shows help and exits."""
        with patch("sys.argv", ["llauncher"]):
            with patch("sys.exit"):
                with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                    from llauncher import __main__

                    __main__.main()

                    # argparse prints error to stderr
                    output = mock_stderr.getvalue()
                    assert "usage:" in output.lower() or "error:" in output.lower()


class TestInvalidCommand:
    """Test invalid command shows error."""

    def test_invalid_command_shows_error(self):
        """Invalid command argument shows error."""
        with patch("sys.argv", ["llauncher", "invalid-command"]):
            with patch("sys.exit"):
                with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
                    from llauncher import __main__

                    __main__.main()

                    stderr_output = mock_stderr.getvalue()
                    assert "invalid choice" in stderr_output.lower()


class TestVersionFlag:
    """Test --version flag displays version and exits."""

    def test_version_flag(self):
        """--version displays version string and exits."""
        with patch("sys.argv", ["llauncher", "--version"]):
            with patch("sys.exit"):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    from llauncher import __main__

                    __main__.main()

                    output = mock_stdout.getvalue()
                    assert "llauncher" in output.lower()
                    assert "0.1.0" in output


class TestDiscoverCommand:
    """Test discover command functionality."""

    def test_discover_no_scripts(self):
        """Discover command with no scripts produces no output."""
        with patch("sys.argv", ["llauncher", "discover"]):
            with patch(
                "llauncher.core.discovery.discover_scripts", return_value=[]
            ):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    from llauncher import __main__

                    __main__.main()

                    output = mock_stdout.getvalue()
                    assert output.strip() == ""

    def test_discover_with_scripts(self):
        """Discover command with scripts shows formatted output."""
        config = ModelConfig.from_dict_unvalidated(
            {
                "name": "test-model",
                "model_path": "/path/to/model.gguf",
                "default_port": 8080,
                "n_gpu_layers": 255,
                "ctx_size": 4096,
            }
        )

        with patch("sys.argv", ["llauncher", "discover"]):
            with patch(
                "llauncher.core.discovery.discover_scripts", return_value=[config]
            ):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    from llauncher import __main__

                    __main__.main()

                    output = mock_stdout.getvalue()
                    assert "test-model" in output
                    assert "/path/to/model.gguf" in output
                    assert "8080" in output
                    assert "255" in output
                    assert "4096" in output

    def test_discover_auto_allocate_port(self):
        """Discover shows 'Auto-allocate' when default_port is None."""
        config = ModelConfig.from_dict_unvalidated(
            {
                "name": "test-model",
                "model_path": "/path/to/model.gguf",
                "default_port": None,
                "n_gpu_layers": 255,
                "ctx_size": 4096,
            }
        )

        with patch("sys.argv", ["llauncher", "discover"]):
            with patch(
                "llauncher.core.discovery.discover_scripts", return_value=[config]
            ):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    from llauncher import __main__

                    __main__.main()

                    output = mock_stdout.getvalue()
                    assert "Auto-allocate" in output


class TestMcpCommand:
    """Test MCP command invokes server main."""

    def test_mcp_command(self):
        """MCP command invokes MCP server main()."""
        with patch("sys.argv", ["llauncher", "mcp"]):
            with patch("llauncher.mcp.server.main") as mock_mcp_main:
                from llauncher import __main__

                __main__.main()

                mock_mcp_main.assert_called_once()


class TestUiCommand:
    """Test UI command prints redirect message and exits."""

    def test_ui_command(self):
        """UI command prints redirect message and exits with code 1."""
        with patch("sys.argv", ["llauncher", "ui"]):
            with patch("sys.exit") as mock_exit:
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    from llauncher import __main__

                    __main__.main()

                    mock_exit.assert_called_once_with(1)
                    output = mock_stdout.getvalue()
                    assert "llauncher-ui" in output.lower()
                    assert "streamlit" in output.lower()


class TestMainGuard:
    """Test __main__ guard works correctly."""

    def test_main_guard(self):
        """When imported, main() is not called automatically."""
        # Simply importing should not execute main()
        # If it did, the test would fail due to argparse errors
        import llauncher.__main__

        # If we get here, main() was not called
        assert True
