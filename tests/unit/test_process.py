"""Tests for llauncher core process management."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
import psutil

from llauncher.core.process import (
    find_available_port,
    build_command,
    start_server,
    stop_server_by_port,
    stop_server_by_pid,
    find_server_by_port,
    find_all_llama_servers,
    stream_logs,
    _tail_file,
    is_port_in_use,
)
from llauncher.models.config import ModelConfig


# Fixtures
@pytest.fixture
def minimal_config():
    """Minimal model config for testing."""
    return ModelConfig.from_dict_unvalidated(
        {
            "name": "test-model",
            "model_path": "/fake/path/model.gguf",
            "n_gpu_layers": 255,
        }
    )


@pytest.fixture
def full_config():
    """Full model config with all optional fields."""
    return ModelConfig.from_dict_unvalidated(
        {
            "name": "full-model",
            "model_path": "/path/to/model.gguf",
            "mmproj_path": "/path/to/mmproj.gguf",
            "default_port": 8080,
            "n_gpu_layers": 255,
            "ctx_size": 4096,
            "threads": 8,
            "threads_batch": 8,
            "ubatch_size": 512,
            "batch_size": 2048,
            "flash_attn": "auto",
            "no_mmap": True,
            "cache_type_k": "f16",
            "cache_type_v": "f16",
            "n_cpu_moe": 4,
            "parallel": 4,
            "temperature": 0.7,
            "top_k": 40,
            "top_p": 0.9,
            "min_p": 0.1,
            "reverse_prompt": "STOP",
            "mlock": True,
            "extra_args": ["--custom-flag", "value"],
        }
    )


class TestFindAvailablePort:
    """Tests for find_available_port function."""

    def test_preferred_port_available(self):
        """Preferred port available - returns immediately."""
        with patch("llauncher.core.process.is_port_in_use", return_value=False):
            success, port, msg = find_available_port(preferred_port=9000)
            assert success is True
            assert port == 9000
            assert "preferred" in msg.lower()

    def test_preferred_port_in_use_first_available(self):
        """Preferred port in use, first scanned port available."""

        def port_in_use(p):
            return p == 9000  # Only 9000 is in use

        with patch("llauncher.core.process.is_port_in_use", side_effect=port_in_use):
            success, port, msg = find_available_port(preferred_port=9000, start=8080, end=8090)
            assert success is True
            assert port == 8080
            assert "auto-allocated" in msg.lower()

    def test_preferred_port_in_use_scan_multiple(self):
        """Preferred port in use, must scan through multiple ports."""

        def port_in_use(p):
            return p in [9000, 8080, 8081, 8082]

        with patch("llauncher.core.process.is_port_in_use", side_effect=port_in_use):
            success, port, msg = find_available_port(preferred_port=9000, start=8080, end=8090)
            assert success is True
            assert port == 8083

    def test_all_ports_in_use(self):
        """All ports in range in use - returns failure."""
        with patch("llauncher.core.process.is_port_in_use", return_value=True):
            success, port, msg = find_available_port(start=8080, end=8082)
            assert success is False
            assert port == 0
            assert "no available" in msg.lower()

    def test_no_preferred_port_first_available(self):
        """No preferred port, first port in range available."""
        with patch("llauncher.core.process.is_port_in_use", return_value=False):
            success, port, msg = find_available_port(start=8080, end=8090)
            assert success is True
            assert port == 8080

    def test_preferred_port_in_range_skipped(self):
        """Preferred port within range is skipped during scan."""

        def port_in_use(p):
            return p == 8085  # Preferred port is in range and in use

        with patch("llauncher.core.process.is_port_in_use", side_effect=port_in_use):
            success, port, msg = find_available_port(preferred_port=8085, start=8080, end=8090)
            assert success is True
            assert port == 8080  # Should get first available, not preferred


class TestBuildCommand:
    """Tests for build_command function."""

    def test_minimal_config(self, minimal_config):
        """Minimal config produces basic command."""
        cmd = build_command(minimal_config, port=8080)
        assert "llama-server" in " ".join(cmd)
        assert "-m" in cmd
        assert minimal_config.model_path in cmd
        assert "--n-gpu-layers" in cmd
        assert str(minimal_config.n_gpu_layers) in cmd
        assert "--host" in cmd
        assert "0.0.0.0" in cmd
        assert "--port" in cmd
        assert "8080" in cmd

    def test_full_config(self, full_config):
        """Full config includes all flags."""
        cmd = build_command(full_config, port=8080)
        cmd_str = " ".join(cmd)

        # Check all fields
        assert "--mmproj" in cmd_str
        assert full_config.mmproj_path in cmd
        assert "--threads" in cmd
        assert str(full_config.threads) in cmd
        assert "--batch-size" in cmd
        assert str(full_config.batch_size) in cmd
        assert "--flash-attn" in cmd
        assert full_config.flash_attn in cmd
        assert "--no-mmap" in cmd
        assert "--cache-type-k" in cmd
        assert full_config.cache_type_k in cmd
        assert "--cache-type-v" in cmd
        assert full_config.cache_type_v in cmd
        assert "--n-cpu-moe" in cmd
        assert str(full_config.n_cpu_moe) in cmd
        assert "--parallel" in cmd
        assert str(full_config.parallel) in cmd
        assert "--temp" in cmd
        assert str(full_config.temperature) in cmd
        assert "--top-k" in cmd
        assert str(full_config.top_k) in cmd
        assert "--top-p" in cmd
        assert str(full_config.top_p) in cmd
        assert "--min-p" in cmd
        assert str(full_config.min_p) in cmd
        assert "--reverse-prompt" in cmd
        assert full_config.reverse_prompt in cmd
        assert "--mlock" in cmd
        assert "--custom-flag" in cmd

    def test_parallel_default_not_included(self, minimal_config):
        """parallel=1 (default) is not included in command."""
        minimal_config.parallel = 1
        cmd = build_command(minimal_config, port=8080)
        assert "--parallel" not in cmd

    def test_extra_args_extended(self, minimal_config):
        """extra_args are extended to command."""
        minimal_config.extra_args = ["--extra1", "val1", "--extra2"]
        cmd = build_command(minimal_config, port=8080)
        assert "--extra1" in cmd
        assert "val1" in cmd
        assert "--extra2" in cmd

    def test_custom_host(self, minimal_config):
        """Custom host parameter is used."""
        cmd = build_command(minimal_config, port=8080, host="127.0.0.1")
        assert "--host" in cmd
        assert "127.0.0.1" in cmd


class TestStartServer:
    """Tests for start_server function."""

    def test_normal_start(self, minimal_config):
        """Normal successful server start."""
        mock_process = MagicMock()
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        with patch("llauncher.core.process.DEFAULT_SERVER_BINARY", mock_bin):
            with patch("subprocess.Popen", return_value=mock_process) as mock_popen:
                with patch("llauncher.core.process.LOG_DIR") as mock_log_dir:
                    mock_log_dir.mkdir = MagicMock()

                    result = start_server(minimal_config, port=8080)

                    assert result == mock_process
                    mock_popen.assert_called_once()
                    call_kwargs = mock_popen.call_args[1]
                    assert call_kwargs.get("start_new_session") is True

    def test_binary_not_found(self, minimal_config):
        """Server binary not found raises FileNotFoundError."""
        mock_bin = MagicMock()
        mock_bin.exists.return_value = False
        mock_bin.__str__ = MagicMock(return_value="/fake/path/llama-server")

        with pytest.raises(FileNotFoundError, match="Server binary not found"):
            start_server(minimal_config, port=8080, server_bin=mock_bin)


class TestStopServer:
    """Tests for stop_server functions."""

    def test_stop_by_port_found(self):
        """Stop server by port when found."""
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with patch("llauncher.core.process.find_server_by_port", return_value=mock_proc):
            with patch("llauncher.core.process.stop_server_by_pid", return_value=True) as mock_stop:
                result = stop_server_by_port(8080)

                assert result is True
                mock_stop.assert_called_once_with(12345)

    def test_stop_by_port_not_found(self):
        """Stop server by port when not found."""
        with patch("llauncher.core.process.find_server_by_port", return_value=None):
            result = stop_server_by_port(8080)
            assert result is False

    def test_stop_by_pid_with_children(self):
        """Stop server by pid terminates children then parent."""
        mock_proc = MagicMock()
        mock_child = MagicMock()
        mock_proc.children.return_value = [mock_child]
        mock_proc.wait.side_effect = psutil.TimeoutExpired(seconds=5, pid=12345)

        with patch("psutil.Process", return_value=mock_proc):
            result = stop_server_by_pid(12345)

            assert result is True
            mock_child.terminate.assert_called_once()
            mock_proc.terminate.assert_called_once()
            mock_proc.kill.assert_called_once()

    def test_stop_by_pid_not_found(self):
        """Stop server by pid when process not found."""
        with patch("psutil.Process", side_effect=psutil.NoSuchProcess(12345, None)):
            result = stop_server_by_pid(12345)
            assert result is False


class TestFindServer:
    """Tests for find_server functions."""

    def test_find_by_port_found(self):
        """Find server by port when found via --port <n> format."""
        mock_proc = MagicMock()
        mock_proc.name.return_value = "llama-server"
        mock_proc.cmdline.return_value = ["llama-server", "--port", "8080"]

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = find_server_by_port(8080)
            assert result == mock_proc

    def test_find_all_servers(self):
        """Find all llama-server processes."""
        mock_proc1 = MagicMock()
        mock_proc1.name.return_value = "llama-server"
        mock_proc1.cmdline.return_value = ["llama-server", "--port", "8080"]

        mock_proc2 = MagicMock()
        mock_proc2.name.return_value = "other"
        mock_proc2.cmdline.return_value = ["other-process"]

        mock_proc3 = MagicMock()
        mock_proc3.name.return_value = "bash"
        mock_proc3.cmdline.return_value = ["bash", "llama-server"]

        with patch("psutil.process_iter", return_value=[mock_proc1, mock_proc2, mock_proc3]):
            results = find_all_llama_servers()
            assert len(results) == 2
            assert mock_proc1 in results
            assert mock_proc3 in results

    def test_find_all_servers_empty(self):
        """Find all llama-server processes when none running."""
        with patch("psutil.process_iter", return_value=[]):
            results = find_all_llama_servers()
            assert results == []


class TestStreamLogs:
    """Tests for stream_logs function."""

    def test_stream_logs_by_pid(self):
        """Stream logs when pid provided and port extracted."""
        mock_proc = MagicMock()
        mock_proc.cmdline.return_value = ["llama-server", "--port", "8080"]
        mock_log_file = MagicMock()

        def glob_side_effect(pattern):
            return [mock_log_file]

        with patch("psutil.Process", return_value=mock_proc):
            with patch("llauncher.core.process._tail_file", return_value=["log line 1"]):
                with patch("llauncher.core.process.LOG_DIR") as mock_log_dir:
                    mock_log_dir.glob.side_effect = glob_side_effect

                    result = stream_logs(pid=12345)

                    assert result == ["log line 1"]

    def test_stream_logs_by_model_name(self):
        """Stream logs when model_name provided."""
        mock_log_file = MagicMock()
        mock_log_file.__str__.return_value = "/fake/logs/test-model-8080.log"

        with patch("llauncher.core.process.LOG_DIR") as mock_log_dir:
            mock_log_dir.glob.return_value = [mock_log_file]
            with patch("llauncher.core.process._tail_file", return_value=["log line 1"]):
                result = stream_logs(model_name="test-model")
                assert result == ["log line 1"]

    def test_stream_logs_not_found(self):
        """Stream logs returns empty when not found."""
        with patch("llauncher.core.process.LOG_DIR") as mock_log_dir:
            mock_log_dir.glob.return_value = []
            result = stream_logs(model_name="nonexistent")
            assert result == []


class TestTailFile:
    """Tests for _tail_file function."""

    def test_tail_file_exists(self, tmp_path):
        """Tail file when it exists."""
        log_file = tmp_path / "test.log"
        log_file.write_text("line1\nline2\nline3\nline4\nline5\n")

        result = _tail_file(log_file, 3)
        assert result == ["line3", "line4", "line5"]

    def test_tail_file_not_exists(self, tmp_path):
        """Tail file when it doesn't exist."""
        log_file = tmp_path / "nonexistent.log"
        result = _tail_file(log_file, 10)
        assert result == []

    def test_tail_file_fewer_lines(self, tmp_path):
        """Tail file returns all lines when fewer than requested."""
        log_file = tmp_path / "test.log"
        log_file.write_text("line1\nline2\n")

        result = _tail_file(log_file, 10)
        assert result == ["line1", "line2"]


class TestIsPortInUse:
    """Tests for is_port_in_use function."""

    def test_port_in_use(self):
        """Port is in use when found in process cmdline."""
        mock_proc = MagicMock()
        mock_proc.cmdline.return_value = ["llama-server", "--port", "8080"]

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = is_port_in_use(8080)
            assert result is True

    def test_port_not_in_use(self):
        """Port is not in use when not found."""
        mock_proc = MagicMock()
        mock_proc.cmdline.return_value = ["llama-server", "--port", "9000"]

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = is_port_in_use(8080)
            assert result is False

    def test_no_partial_match(self):
        """Port 8080 does not match --port 80800."""
        mock_proc = MagicMock()
        mock_proc.cmdline.return_value = ["llama-server", "--port", "80800"]

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = is_port_in_use(8080)
            assert result is False
