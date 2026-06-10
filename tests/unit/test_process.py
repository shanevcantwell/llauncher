"""Tests for llauncher core process management."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open
import psutil

from llauncher.core.process import (
    find_available_port,
    build_command,
    log_stem_for,
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
            "repeat_penalty": 1.5,
            "reverse_prompt": "STOP",
            "mlock": True,
            "extra_args": "--custom-flag value",
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
        assert "--repeat-penalty" in cmd
        assert str(full_config.repeat_penalty) in cmd
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
        """extra_args string is parsed and extended to command."""
        minimal_config.extra_args = "--extra1 val1 --extra2"
        cmd = build_command(minimal_config, port=8080)
        assert "--extra1" in cmd
        assert "val1" in cmd
        assert "--extra2" in cmd

    def test_extra_args_with_quoted_strings(self, minimal_config):
        """extra_args properly handles quoted strings with spaces."""
        minimal_config.extra_args = '--reverse-prompt "You are helpful"'
        cmd = build_command(minimal_config, port=8080)
        assert "--reverse-prompt" in cmd
        assert "You are helpful" in cmd

    def test_custom_host(self, minimal_config):
        """Custom host parameter is used."""
        cmd = build_command(minimal_config, port=8080, host="127.0.0.1")
        assert "--host" in cmd
        assert "127.0.0.1" in cmd

    def test_repeat_penalty_none_not_included(self, minimal_config):
        """repeat_penalty=None does not include --repeat-penalty flag."""
        minimal_config.repeat_penalty = None
        cmd = build_command(minimal_config, port=8080)
        assert "--repeat-penalty" not in cmd

    def test_repeat_penalty_included(self, minimal_config):
        """repeat_penalty=1.5 includes --repeat-penalty flag with correct value."""
        minimal_config.repeat_penalty = 1.5
        cmd = build_command(minimal_config, port=8080)
        assert "--repeat-penalty" in cmd
        assert "1.5" in cmd


def _alias_value(cmd: list[str]) -> str:
    """Return the argv token immediately following ``--alias``."""
    idx = cmd.index("--alias")
    assert idx + 1 < len(cmd), "--alias present but missing its value"
    return cmd[idx + 1]


class TestBuildCommandAlias:
    """Issue #120 (EMIT-CANONICAL): ``build_command`` must pass
    ``--alias <ModelConfig.name>`` so ``GET /v1/models`` reports the
    canonical minted name, byte-for-byte — no transformation, no
    sanitization. Ecosystem routers (local-inference-pool) match servers
    against this id.
    """

    @staticmethod
    def _config_named(name: str, **overrides) -> ModelConfig:
        data = {
            "name": name,
            "model_path": "/fake/path/model.gguf",
            **overrides,
        }
        return ModelConfig.from_dict_unvalidated(data)

    def test_alias_present_with_exact_name(self, minimal_config):
        """The spawn argv contains ``--alias`` immediately followed by
        the exact model name.
        """
        cmd = build_command(minimal_config, port=8080)
        assert "--alias" in cmd
        assert _alias_value(cmd) == "test-model"

    def test_alias_present_in_full_config(self, full_config):
        """Alias is emitted regardless of which optional fields are set."""
        cmd = build_command(full_config, port=8080)
        assert _alias_value(cmd) == "full-model"

    def test_alias_name_with_spaces_untransformed(self):
        """A name containing spaces survives as a single argv token —
        list-form argv needs no quoting and must not introduce any.
        """
        cfg = self._config_named("My Local Model 7B")
        cmd = build_command(cfg, port=8080)
        assert _alias_value(cmd) == "My Local Model 7B"

    def test_alias_name_with_unicode_untransformed(self):
        """Unicode names are emitted byte-for-byte."""
        name = "qwen3-中文-モデル-β"
        cfg = self._config_named(name)
        cmd = build_command(cfg, port=8080)
        assert _alias_value(cmd) == name

    def test_alias_not_sanitized_like_log_names(self):
        """The lossy ``[^\\w-] -> _`` transform used for log *filenames*
        (``log_path_for``) must NOT touch the alias: a name with dots
        and pluses is emitted verbatim, not collapsed to underscores.
        """
        name = "LFM2-350M-Pro.f16+test"
        cfg = self._config_named(name)
        cmd = build_command(cfg, port=8080)
        assert _alias_value(cmd) == name
        assert "LFM2-350M-Pro_f16_test" not in cmd

    def test_alias_emitted_exactly_once_with_extra_args(self):
        """Benign ``extra_args`` must not produce a second ``--alias``;
        the launcher's emission is the only one in argv.
        """
        cfg = self._config_named(
            "extra-args-model", extra_args="--log-disable --verbose"
        )
        cmd = build_command(cfg, port=8080)
        assert cmd.count("--alias") == 1
        assert _alias_value(cmd) == "extra-args-model"
        # extra_args still land after the managed flags
        assert "--log-disable" in cmd
        assert "--verbose" in cmd

    def test_alias_cannot_be_overridden_via_extra_args(self, minimal_config):
        """Launcher-owned flag stays launcher-owned: ``extra_args``
        carrying ``--alias`` is rejected by the C7 deny-list before
        ``build_command`` ever sees it (both bare and equals forms).
        """
        with pytest.raises(ValueError, match="--alias"):
            minimal_config.extra_args = "--alias impostor"
        with pytest.raises(ValueError, match="--alias"):
            minimal_config.extra_args = "--alias=impostor"
        # Config unchanged; argv still carries only the minted name.
        cmd = build_command(minimal_config, port=8080)
        assert cmd.count("--alias") == 1
        assert _alias_value(cmd) == "test-model"


class TestStartServer:
    """Tests for start_server function."""

    def test_normal_start(self, minimal_config):
        """Normal successful server start.

        Patches ``log_rotation.rotate_if_needed`` to a no-op so the
        MagicMock ``LOG_DIR`` doesn't break the new ADR-013 rotation
        path (which calls ``path.stat().st_size``).
        """
        mock_process = MagicMock()
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        with patch("llauncher.core.process.DEFAULT_SERVER_BINARY", mock_bin), \
             patch("subprocess.Popen", return_value=mock_process) as mock_popen, \
             patch("llauncher.core.process.LOG_DIR") as mock_log_dir, \
             patch(
                 "llauncher.core.process.log_rotation.rotate_if_needed",
                 return_value=False,
             ):
            mock_log_dir.mkdir = MagicMock()

            result = start_server(minimal_config, port=8080)

            assert result == mock_process
            mock_popen.assert_called_once()
            call_kwargs = mock_popen.call_args[1]
            assert call_kwargs.get("start_new_session") is True
            # Issue #120 (EMIT-CANONICAL): the spawned argv carries the
            # canonical minted name via --alias, byte-for-byte.
            spawned_argv = mock_popen.call_args[0][0]
            assert _alias_value(spawned_argv) == minimal_config.name

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
                mock_stop.assert_called_once_with(
                    12345, child_grace_s=None, grace_s=None
                )

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

    def test_stop_by_pid_kills_children_surviving_grace(self):
        """Issue #140: a child that outlives its SIGTERM grace is SIGKILLed.

        Previously only the main process escalated to ``kill()``; a child
        ignoring SIGTERM leaked past the stop. ``wait_procs`` is faked so
        no real grace period elapses.
        """
        mock_proc = MagicMock()
        survivor = MagicMock()
        deceased = MagicMock()
        mock_proc.children.return_value = [survivor, deceased]

        with patch("psutil.Process", return_value=mock_proc), patch(
            "llauncher.core.process.psutil.wait_procs",
            return_value=([deceased], [survivor]),
        ) as mock_wait:
            result = stop_server_by_pid(12345, child_grace_s=0.01, grace_s=0.01)

        assert result is True
        survivor.terminate.assert_called_once()
        survivor.kill.assert_called_once()  # grace expired → no leak
        deceased.kill.assert_not_called()  # exited within grace
        mock_wait.assert_called_once_with([survivor, deceased], timeout=0.01)

    def test_stop_by_pid_child_gone_before_kill_is_tolerated(self):
        """A survivor that exits between wait and kill is success, not error."""
        mock_proc = MagicMock()
        survivor = MagicMock()
        survivor.kill.side_effect = psutil.NoSuchProcess(54321, None)
        mock_proc.children.return_value = [survivor]

        with patch("psutil.Process", return_value=mock_proc), patch(
            "llauncher.core.process.psutil.wait_procs",
            return_value=([], [survivor]),
        ):
            result = stop_server_by_pid(12345, child_grace_s=0.01, grace_s=0.01)

        assert result is True
        mock_proc.terminate.assert_called_once()

    def test_stop_by_pid_grace_defaults_read_from_settings_at_call_time(self):
        """Grace periods come from settings when not passed (issue #140).

        Read at call time — not captured at import — so env-configured
        profiles and test patches both take effect.
        """
        mock_proc = MagicMock()
        mock_proc.children.return_value = []

        with patch("psutil.Process", return_value=mock_proc), patch(
            "llauncher.core.process.psutil.wait_procs",
            return_value=([], []),
        ) as mock_wait, patch(
            "llauncher.core.process.settings.LLAUNCHER_STOP_CHILD_GRACE_S", 0.25
        ), patch(
            "llauncher.core.process.settings.LLAUNCHER_STOP_GRACE_S", 0.5
        ):
            result = stop_server_by_pid(12345)

        assert result is True
        mock_wait.assert_called_once_with([], timeout=0.25)
        mock_proc.wait.assert_called_once_with(timeout=0.5)

    def test_stop_by_pid_explicit_grace_overrides_settings(self):
        """Explicit keyword grace wins over the settings defaults."""
        mock_proc = MagicMock()
        mock_proc.children.return_value = []

        with patch("psutil.Process", return_value=mock_proc), patch(
            "llauncher.core.process.psutil.wait_procs",
            return_value=([], []),
        ) as mock_wait:
            result = stop_server_by_pid(12345, child_grace_s=0.01, grace_s=0.02)

        assert result is True
        mock_wait.assert_called_once_with([], timeout=0.01)
        mock_proc.wait.assert_called_once_with(timeout=0.02)

    def test_stop_by_port_forwards_grace_to_stop_by_pid(self):
        """``stop_server_by_port`` propagates the grace knobs (issue #140)."""
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        with patch(
            "llauncher.core.process.find_server_by_port", return_value=mock_proc
        ), patch(
            "llauncher.core.process.stop_server_by_pid", return_value=True
        ) as mock_stop:
            result = stop_server_by_port(8080, child_grace_s=0.01, grace_s=0.02)

        assert result is True
        mock_stop.assert_called_once_with(
            12345, child_grace_s=0.01, grace_s=0.02
        )


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

    def test_port_in_use_equals_format(self):
        """Port is in use when found with --port=8080 format."""
        mock_proc = MagicMock()
        mock_proc.cmdline.return_value = ["llama-server", "--port=8080"]

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = is_port_in_use(8080)
            assert result is True


class TestFindAvailablePort:
    """Additional tests for find_available_port edge cases."""

    def test_blacklisted_port_skipped(self):
        """Blacklisted ports are skipped during allocation."""
        def port_in_use(p):
            return p == 9005  # Only 9005 is in use

        # Mock BLACKLISTED_PORTS to include 9000-9002
        with patch("llauncher.core.process.is_port_in_use", side_effect=port_in_use):
            with patch("llauncher.core.process.BLACKLISTED_PORTS", [9000, 9001, 9002]):
                # 9000-9002 are blacklisted, 9003-9004 available, 9005 in use
                success, port, msg = find_available_port(start=9000, end=9010)
                assert success is True
                assert port == 9003  # Should skip blacklisted 9000-9002
                assert "auto-allocated" in msg.lower()

    def test_preferred_port_skipped_during_scan(self):
        """Preferred port is skipped during range scan if already tried.

        This specifically tests line 52 where preferred_port is within the
        scan range and gets skipped because it was already tried.
        """
        preferred = 8085
        call_count = [0]  # Track calls to verify line 52 is hit

        def port_in_use(p):
            # Track when we check the preferred port during scan
            if p == preferred:
                call_count[0] += 1
            # Preferred port 8085 is in use (failed initial check)
            # Ports 8080-8084 are also in use
            # Port 8086 is available
            return p in [8085, 8080, 8081, 8082, 8083, 8084]

        with patch("llauncher.core.process.is_port_in_use", side_effect=port_in_use):
            # Preferred 8085 is in the range [8080, 8090] and was already tried
            # The scan will encounter 8085 at iteration 5 and should skip it via line 52
            # Line 52 should prevent is_port_in_use from being called for 8085 during scan
            success, port, msg = find_available_port(preferred_port=preferred, start=8080, end=8090)
            assert success is True
            assert port == 8086  # First available after skipping preferred and in-use ports
            assert "auto-allocated" in msg.lower()
            # is_port_in_use should be called once for initial preferred check,
            # then NOT called again for 8085 during scan (line 52 skips it)
            assert call_count[0] == 1, f"Expected 1 call (initial check only), got {call_count[0]}"

    def test_preferred_port_in_range_but_blacklisted(self):
        """Preferred port in scan range but blacklisted gets skipped twice."""
        def port_in_use(p):
            return p == 8090  # Only 8090 is in use

        with patch("llauncher.core.process.is_port_in_use", side_effect=port_in_use):
            with patch("llauncher.core.process.BLACKLISTED_PORTS", [8085]):
                # Preferred 8085 is blacklisted, so initial check fails
                # Scan starts at 8080, finds it available
                success, port, msg = find_available_port(preferred_port=8085, start=8080, end=8090)
                assert success is True
                assert port == 8080  # First available in range

    def test_no_available_ports_returns_failure(self):
        """Returns failure when all ports in range are in use."""
        with patch("llauncher.core.process.is_port_in_use", return_value=True):
            success, port, msg = find_available_port(start=8080, end=8082)
            assert success is False
            assert port == 0
            assert "no available" in msg.lower()


class TestIsPortInUseExceptions:
    """Tests for is_port_in_use exception handling."""

    def test_is_port_in_use_access_denied(self):
        """AccessDenied exception is handled gracefully."""
        mock_proc = MagicMock()
        mock_proc.cmdline.side_effect = psutil.AccessDenied(12345)

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = is_port_in_use(8080)
            assert result is False  # No port found, exception handled

    def test_is_port_in_use_no_such_process(self):
        """NoSuchProcess exception is handled gracefully."""
        mock_proc = MagicMock()
        mock_proc.cmdline.side_effect = psutil.NoSuchProcess(12345, None)

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = is_port_in_use(8080)
            assert result is False  # No port found, exception handled


class TestWaitForServerReady:
    """Tests for wait_for_server_ready function."""

    def test_wait_for_server_ready_success(self):
        """Server becomes ready within timeout."""
        from llauncher.core.process import wait_for_server_ready

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        def mock_socket(*args, **kwargs):
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0  # Port open
            mock_sock.settimeout = MagicMock()
            mock_sock.close = MagicMock()
            return mock_sock

        # socket is imported inside the function, so patch at module level
        with patch("llauncher.core.process.find_server_by_port", return_value=mock_proc):
            with patch("llauncher.core.process.stream_logs", return_value=["server started and listening"]):
                with patch("socket.socket", side_effect=mock_socket):
                    with patch("time.sleep"):  # Skip actual sleep
                        is_ready, logs = wait_for_server_ready(8080, timeout=2, check_interval=0.1)

                        assert is_ready is True
                        assert logs == ["server started and listening"]

    def test_wait_for_server_ready_timeout(self):
        """Server does not become ready within timeout."""
        from llauncher.core.process import wait_for_server_ready

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        def mock_socket(*args, **kwargs):
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0  # Port open but no ready indicator
            mock_sock.settimeout = MagicMock()
            mock_sock.close = MagicMock()
            return mock_sock

        with patch("llauncher.core.process.find_server_by_port", return_value=mock_proc):
            with patch("llauncher.core.process.stream_logs", return_value=["loading model..."]):
                with patch("socket.socket", side_effect=mock_socket):
                    with patch("time.sleep"):  # Skip actual sleep
                        is_ready, logs = wait_for_server_ready(8080, timeout=0.2, check_interval=0.1)

                        assert is_ready is False
                        assert logs == ["loading model..."]

    def test_wait_for_server_ready_port_never_opens(self):
        """Port never opens within timeout."""
        from llauncher.core.process import wait_for_server_ready

        def mock_socket(*args, **kwargs):
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1  # Port closed
            mock_sock.settimeout = MagicMock()
            mock_sock.close = MagicMock()
            return mock_sock

        with patch("llauncher.core.process.find_server_by_port", return_value=None):
            with patch("socket.socket", side_effect=mock_socket):
                with patch("time.sleep"):  # Skip actual sleep
                    is_ready, logs = wait_for_server_ready(8080, timeout=0.2, check_interval=0.1)

                    assert is_ready is False
                    assert logs == []

    def test_wait_for_server_ready_os_error(self):
        """OSError during socket connection is handled gracefully."""
        from llauncher.core.process import wait_for_server_ready

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        def mock_socket(*args, **kwargs):
            mock_sock = MagicMock()
            mock_sock.connect_ex.side_effect = OSError("Network unreachable")
            mock_sock.settimeout = MagicMock()
            mock_sock.close = MagicMock()
            return mock_sock

        with patch("llauncher.core.process.find_server_by_port", return_value=mock_proc):
            with patch("llauncher.core.process.stream_logs", return_value=[]):
                with patch("socket.socket", side_effect=mock_socket):
                    with patch("time.sleep"):  # Skip actual sleep
                        is_ready, logs = wait_for_server_ready(8080, timeout=0.2, check_interval=0.1)

                        assert is_ready is False
                        assert logs == []


class TestStopServerExceptions:
    """Tests for stop_server exception handling."""

    def test_stop_by_pid_no_such_process_during_children(self):
        """NoSuchProcess during children termination is handled."""
        mock_proc = MagicMock()
        mock_proc.children.side_effect = psutil.NoSuchProcess(12345, None)

        with patch("psutil.Process", return_value=mock_proc):
            result = stop_server_by_pid(12345)

            # Should still try to terminate main process
            assert result is True
            mock_proc.terminate.assert_called_once()


class TestFindServerExceptions:
    """Tests for find_server exception handling."""

    def test_find_all_servers_zombie_process(self):
        """ZombieProcess exception is handled in find_all_llama_servers."""
        mock_proc = MagicMock()
        mock_proc.name.return_value = "llama-server"
        mock_proc.cmdline.side_effect = psutil.ZombieProcess(12345, None)

        with patch("psutil.process_iter", return_value=[mock_proc]):
            results = find_all_llama_servers()
            assert results == []  # Zombie process skipped

    def test_find_server_by_port_equals_format(self):
        """Find server with --port=8080 format."""
        mock_proc = MagicMock()
        mock_proc.name.return_value = "llama-server"
        mock_proc.cmdline.return_value = ["llama-server", "--port=8080"]

        with patch("psutil.process_iter", return_value=[mock_proc]):
            result = find_server_by_port(8080)
            assert result == mock_proc


class TestStreamLogsExceptions:
    """Tests for stream_logs exception handling."""

    def test_stream_logs_no_such_process(self):
        """NoSuchProcess exception when getting cmdline is handled."""
        mock_proc = MagicMock()
        mock_proc.cmdline.side_effect = psutil.NoSuchProcess(12345, None)

        with patch("psutil.Process", return_value=mock_proc):
            with patch("llauncher.core.process.LOG_DIR") as mock_log_dir:
                mock_log_dir.glob.return_value = []
                result = stream_logs(pid=12345)
                assert result == []

    def test_stream_logs_access_denied(self):
        """AccessDenied exception when getting cmdline is handled."""
        mock_proc = MagicMock()
        mock_proc.cmdline.side_effect = psutil.AccessDenied(12345)

        with patch("psutil.Process", return_value=mock_proc):
            with patch("llauncher.core.process.LOG_DIR") as mock_log_dir:
                mock_log_dir.glob.return_value = []
                result = stream_logs(pid=12345)
                assert result == []


class TestTailFileExceptions:
    """Tests for _tail_file exception handling."""

    def test_tail_file_os_error(self, tmp_path):
        """OSError during file read is handled."""
        log_file = tmp_path / "test.log"
        log_file.write_text("line1\nline2\n")

        with patch("builtins.open", side_effect=OSError("Permission denied")):
            result = _tail_file(log_file, 10)
            assert result == []

    def test_tail_file_invalid_utf8_is_replaced(self, tmp_path):
        """Invalid UTF-8 bytes are silently replaced, not raised.

        Per ADR-013, ``_tail_file`` reads bytes and decodes with
        ``errors="replace"`` so the historical UnicodeError path no
        longer exists. This test verifies the new behavior: a file with
        invalid UTF-8 returns *something* (replacement chars) rather
        than the empty list the old implementation would have produced.
        """
        log_file = tmp_path / "test.log"
        log_file.write_bytes(b"\xff\xfe invalid utf8\nfollowing line\n")

        result = _tail_file(log_file, 10)

        # Invalid bytes get the U+FFFD replacement; the rest of the
        # content is preserved and split on newline boundaries.
        assert result, "expected at least one line, not the empty list"
        assert "following line" in result[-1]


class TestTailFileBoundedRead:
    """ADR-013 bounded-tail tests — _tail_file must not slurp the whole file."""

    def test_returns_last_n_lines_for_small_file(self, tmp_path):
        """Sanity: a small file is returned in full when N >= line count."""
        log_file = tmp_path / "small.log"
        log_file.write_text("a\nb\nc\n")

        assert _tail_file(log_file, 10) == ["a", "b", "c"]

    def test_caps_at_lines_requested(self, tmp_path):
        """Returns exactly the last N lines, not more."""
        log_file = tmp_path / "many.log"
        log_file.write_text("\n".join(f"line-{i}" for i in range(50)) + "\n")

        result = _tail_file(log_file, 5)

        assert result == ["line-45", "line-46", "line-47", "line-48", "line-49"]

    def test_zero_or_negative_lines_returns_empty(self, tmp_path):
        """Edge case: lines<=0 short-circuits before any read."""
        log_file = tmp_path / "x.log"
        log_file.write_text("a\nb\nc\n")

        assert _tail_file(log_file, 0) == []
        assert _tail_file(log_file, -1) == []

    def test_drops_partial_first_line_when_window_seeks_mid_file(self, tmp_path):
        """The seek-from-end window almost always cuts mid-line; that
        partial first line must be dropped so callers don't see a
        truncated record.

        Sizing math: AVG=160, ``lines=2`` → window=640B. With ~80-byte
        padded lines, that window holds ~8 whole lines; the file is
        100 lines × ~80B = ~8 KiB, well above the window. So we get
        bounded read AND enough room for the trailing two lines to
        survive the partial-first-line drop.
        """
        log_file = tmp_path / "long.log"
        body = "\n".join(f"line-{i:03d}:{'X' * 70}" for i in range(100)) + "\n"
        log_file.write_text(body)
        assert log_file.stat().st_size > 4000, "fixture too small to exercise the window"

        result = _tail_file(log_file, 2)

        # The last two whole lines are preserved verbatim.
        assert len(result) == 2
        assert result[-1].startswith("line-099:")
        assert result[-2].startswith("line-098:")
        # No partial fragment leaked in: every returned line starts with
        # the canonical ``line-NNN:`` prefix.
        for line in result:
            assert line.startswith("line-"), (
                f"partial line slipped through: {line[:80]!r}"
            )

    def test_does_not_load_entire_huge_file(self, tmp_path):
        """A 5 MiB log requesting 10 lines must read kilobytes, not megabytes."""
        log_file = tmp_path / "huge.log"
        # 5 MiB of 100-byte lines = ~52 480 lines
        line = ("a" * 99) + "\n"
        log_file.write_text(line * 52_480)

        # Track how many bytes were read by spying on the open() return.
        real_open = open
        bytes_read = []

        def tracking_open(path, mode="r", *args, **kwargs):
            f = real_open(path, mode, *args, **kwargs)
            real_read = f.read

            def spy_read(*a, **kw):
                data = real_read(*a, **kw)
                if isinstance(data, (bytes, str)):
                    bytes_read.append(len(data))
                return data

            f.read = spy_read
            return f

        with patch("builtins.open", side_effect=tracking_open):
            result = _tail_file(log_file, 10)

        assert len(result) == 10
        # 10 lines × 160 avg × 2 = 3200 bytes window. Read budget should
        # comfortably stay under 10 KiB even with rounding/encoding overhead.
        assert sum(bytes_read) < 10_240, (
            f"_tail_file read {sum(bytes_read)} bytes for 10 lines from a "
            f"{log_file.stat().st_size}-byte file; bounded-tail regression."
        )


class TestStartServerLogsLifecycle:
    """ADR-013 — start_server uses append mode, writes a banner, rotates first."""

    def test_appends_banner_and_preserves_prior_content(
        self, tmp_path, minimal_config
    ):
        """A pre-existing log file is preserved; the banner appends to it."""
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        # Stem derived via the one mint (sanitized name + short hash).
        existing_log = log_dir / f"{log_stem_for('test-model')}-8081.log"
        existing_log.write_text("previous run line 1\nprevious run line 2\n")

        with patch("llauncher.core.process.DEFAULT_SERVER_BINARY", mock_bin), \
             patch("llauncher.core.process.LOG_DIR", log_dir), \
             patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            start_server(minimal_config, port=8081)

        contents = existing_log.read_text(encoding="utf-8")
        assert "previous run line 1" in contents
        assert "previous run line 2" in contents
        assert "=== started at" in contents
        assert "port=8081" in contents
        # Banner must come AFTER the previous-run lines.
        banner_idx = contents.index("=== started at")
        assert contents.index("previous run line 2") < banner_idx

    def test_rotates_when_existing_log_exceeds_max_bytes(
        self, tmp_path, minimal_config
    ):
        """An oversized log triggers rotation before the new run appends."""
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / f"{log_stem_for('test-model')}-8081.log"
        # Write 200 bytes of content; we'll cap rotation at 100 below so
        # this file gets rotated.
        log_file.write_text("X" * 200)

        with patch("llauncher.core.process.DEFAULT_SERVER_BINARY", mock_bin), \
             patch("llauncher.core.process.LOG_DIR", log_dir), \
             patch("llauncher.core.process.settings.LAUNCHER_LOG_MAX_BYTES", 100), \
             patch("llauncher.core.process.settings.LAUNCHER_LOG_KEEP", 3), \
             patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            start_server(minimal_config, port=8081)

        rotated = log_dir / f"{log_stem_for('test-model')}-8081.log.1"
        assert rotated.exists(), "rotation did not happen; .log.1 missing"
        assert rotated.read_text() == "X" * 200
        # New live log contains only the banner (no previous content).
        assert "X" not in log_file.read_text(encoding="utf-8")
        assert "=== started at" in log_file.read_text(encoding="utf-8")
