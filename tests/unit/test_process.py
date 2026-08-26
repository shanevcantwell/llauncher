"""Tests for llauncher core process management."""

import logging
import importlib
import re
from datetime import datetime, timedelta, timezone

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
    discover_all,
    verify_pid,
    ServerProcessInfo,
    invalidate_process_scan_cache,
    stream_logs,
    _tail_file,
    is_port_in_use,
    DENIED_EXTRA_ARG_FLAGS,
    DeniedExtraArgError,
    ExtraArgsError,
    MalformedExtraArgsError,
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
    """Full model config with all llauncher-owned fields set, plus a
    representative extra_args passthrough (ADR-026 / issue #477: the 16
    llama-server mirror fields no longer exist as ModelConfig fields)."""
    return ModelConfig.from_dict_unvalidated(
        {
            "name": "full-model",
            "model_path": "/path/to/model.gguf",
            "mmproj_path": "/path/to/mmproj.gguf",
            "default_port": 8080,
            "n_gpu_layers": 255,
            "ctx_size": 4096,
            "parallel": 4,
            "extra_args": (
                "--threads 8 --threads-batch 8 --ubatch-size 512 "
                "--batch-size 2048 --flash-attn auto --no-mmap "
                "--cache-type-k f16 --cache-type-v f16 --n-cpu-moe 4 "
                "--temp 0.7 --top-k 40 --top-p 0.9 --min-p 0.1 "
                "--repeat-penalty 1.5 --reverse-prompt STOP --mlock "
                "--custom-flag value"
            ),
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

        # Pin the blacklist empty so the scan is independent of the
        # ``BLACKLISTED_PORTS`` env var (which defaults to [] but is set on
        # some dev hosts); 8080 is then the first allocatable port.
        with patch("llauncher.core.process.is_port_in_use", side_effect=port_in_use), \
             patch("llauncher.core.process.BLACKLISTED_PORTS", []):
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
        with patch("llauncher.core.process.is_port_in_use", return_value=False), \
             patch("llauncher.core.process.BLACKLISTED_PORTS", []):
            success, port, msg = find_available_port(start=8080, end=8090)
            assert success is True
            assert port == 8080

    def test_preferred_port_in_range_skipped(self):
        """Preferred port within range is skipped during scan."""

        def port_in_use(p):
            return p == 8085  # Preferred port is in range and in use

        with patch("llauncher.core.process.is_port_in_use", side_effect=port_in_use), \
             patch("llauncher.core.process.BLACKLISTED_PORTS", []):
            success, port, msg = find_available_port(preferred_port=8085, start=8080, end=8090)
            assert success is True
            # First allocatable in range, not the preferred (8085 is in use).
            assert port == 8080


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
        """Full config includes the owned fields and the extra_args passthrough."""
        cmd = build_command(full_config, port=8080)
        cmd_str = " ".join(cmd)

        # Owned fields, rendered from their dedicated ModelConfig fields.
        assert "--mmproj" in cmd_str
        assert full_config.mmproj_path in cmd
        assert "--parallel" in cmd
        assert str(full_config.parallel) in cmd

        # Everything else (ADR-026 / issue #477 dropped fields) arrives
        # verbatim through extra_args, appended once, unmodified.
        for token in (
            "--threads", "8", "--threads-batch", "--ubatch-size", "512",
            "--batch-size", "2048", "--flash-attn", "auto", "--no-mmap",
            "--cache-type-k", "f16", "--cache-type-v", "--n-cpu-moe", "4",
            "--temp", "0.7", "--top-k", "40", "--top-p", "0.9", "--min-p",
            "0.1", "--repeat-penalty", "1.5", "--reverse-prompt", "STOP",
            "--mlock", "--custom-flag", "value",
        ):
            assert token in cmd, f"expected {token!r} from extra_args in {cmd}"

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
        """extra_args properly handles quoted strings with spaces.

        Uses ``--chat-template`` (not a llauncher-managed flag) so the test
        exercises shlex quote handling without tripping the issue #156
        managed-flag collision guard. ``--reverse-prompt`` would now be
        rejected on assignment because llauncher emits it natively.
        """
        minimal_config.extra_args = '--chat-template "You are helpful"'
        cmd = build_command(minimal_config, port=8080)
        assert "--chat-template" in cmd
        assert "You are helpful" in cmd

    def test_custom_host(self, minimal_config):
        """Custom host parameter is used."""
        cmd = build_command(minimal_config, port=8080, host="127.0.0.1")
        assert "--host" in cmd
        assert "127.0.0.1" in cmd

    def test_repeat_penalty_absent_by_default(self, minimal_config):
        """No --repeat-penalty flag when extra_args doesn't carry one.

        Per ADR-026 / issue #477, repeat_penalty is no longer a dedicated
        ModelConfig field — it's reachable only through extra_args.
        """
        cmd = build_command(minimal_config, port=8080)
        assert "--repeat-penalty" not in cmd

    def test_repeat_penalty_included_via_extra_args(self, minimal_config):
        """--repeat-penalty 1.5 set via extra_args passes through verbatim."""
        minimal_config.extra_args = "--repeat-penalty 1.5"
        cmd = build_command(minimal_config, port=8080)
        assert "--repeat-penalty" in cmd
        assert "1.5" in cmd

    def test_metrics_default_on(self, minimal_config):
        """Issue #169: metrics defaults to True and emits --metrics."""
        assert minimal_config.metrics is True
        cmd = build_command(minimal_config, port=8080)
        assert "--metrics" in cmd

    def test_metrics_disabled_omits_flag(self, minimal_config):
        """Issue #169: metrics=False must not emit --metrics."""
        minimal_config.metrics = False
        cmd = build_command(minimal_config, port=8080)
        assert "--metrics" not in cmd

    def test_slots_default_off_emits_no_slots(self, minimal_config):
        """Issue #179 SP-1: slots defaults to False and emits --no-slots.

        llama-server's own binary default for --slots is ENABLED (PM-2
        de-risk finding) — the opposite of a safe default, since /slots
        exposes per-slot prompt text. The launcher must emit the flag
        explicitly rather than rely on the binary default.
        """
        assert minimal_config.slots is False
        cmd = build_command(minimal_config, port=8080)
        assert "--no-slots" in cmd
        assert "--slots" not in cmd

    def test_slots_enabled_emits_slots_flag(self, minimal_config):
        """Issue #179 SP-1: slots=True emits --slots, not --no-slots."""
        minimal_config.slots = True
        cmd = build_command(minimal_config, port=8080)
        assert "--slots" in cmd
        assert "--no-slots" not in cmd


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
        carrying ``--alias`` is accepted by ``ModelConfig`` (ADR-026 / issue
        #477: no pydantic content validation) but rejected by
        ``build_command`` — the single, launch-time enforcement point —
        before argv is ever assembled (both bare and equals forms).
        """
        minimal_config.extra_args = "--alias impostor"
        with pytest.raises(DeniedExtraArgError, match="--alias"):
            build_command(minimal_config, port=8080)

        minimal_config.extra_args = "--alias=impostor"
        with pytest.raises(DeniedExtraArgError, match="--alias"):
            build_command(minimal_config, port=8080)

        # A benign extra_args still produces the minted name, unharmed.
        minimal_config.extra_args = ""
        cmd = build_command(minimal_config, port=8080)
        assert cmd.count("--alias") == 1
        assert _alias_value(cmd) == "test-model"


class TestBuildCommandDenyList:
    """ADR-026 / issue #477: the llauncher-owned extra_args deny-list is
    enforced exactly once, at launch time, in ``build_command`` — no
    pydantic content validation exists any more (``ModelConfig`` accepts
    any string). Covers both bare and ``=``-form for every denied flag.
    """

    @pytest.mark.parametrize("flag", sorted(DENIED_EXTRA_ARG_FLAGS))
    def test_each_denied_flag_raises(self, minimal_config, flag) -> None:
        minimal_config.extra_args = f"{flag} sentinel-value"
        with pytest.raises(DeniedExtraArgError, match=re.escape(flag)):
            build_command(minimal_config, port=8080)

    @pytest.mark.parametrize("flag", sorted(DENIED_EXTRA_ARG_FLAGS))
    def test_each_denied_flag_equals_form_raises(self, minimal_config, flag) -> None:
        minimal_config.extra_args = f"{flag}=sentinel-value"
        with pytest.raises(DeniedExtraArgError, match=re.escape(flag)):
            build_command(minimal_config, port=8080)

    def test_modelconfig_itself_accepts_denied_flags_unvalidated(self) -> None:
        """ADR-026: constructing/assigning a denied flag into extra_args
        does NOT raise — only build_command does. This is the "disable
        pydantic for extra_args" directive, made concrete.
        """
        cfg = ModelConfig.from_dict_unvalidated({
            "name": "m",
            "model_path": "/fake/does-not-matter.gguf",
            "extra_args": "--api-key leaked --port 9999",
        })
        assert cfg.extra_args == "--api-key leaked --port 9999"
        cfg.extra_args = "--alias sneaky"
        assert cfg.extra_args == "--alias sneaky"

    def test_port_denied(self, minimal_config) -> None:
        minimal_config.extra_args = "--port 9999"
        with pytest.raises(DeniedExtraArgError, match="--port"):
            build_command(minimal_config, port=8080)

    def test_benign_flag_not_denied(self, minimal_config) -> None:
        minimal_config.extra_args = "--log-disable --flash-attn on"
        cmd = build_command(minimal_config, port=8080)
        assert "--log-disable" in cmd
        assert "--flash-attn" in cmd

    def test_denial_names_the_offending_config_entry(self) -> None:
        """Issue #462: the deny-list message must name the config entry
        it belongs to, not just the flag — in a multi-model registry an
        anonymous message is unactionable. Two configs, only one carries
        a denied flag: the raised message names that one and not the
        other.
        """
        clean = ModelConfig.from_dict_unvalidated({
            "name": "qwen3.8-27b",
            "model_path": "/fake/clean.gguf",
        })
        offending = ModelConfig.from_dict_unvalidated({
            "name": "other-model",
            "model_path": "/fake/offending.gguf",
            "extra_args": "--api-key leaked",
        })

        # The clean config builds without incident.
        build_command(clean, port=8080)

        with pytest.raises(DeniedExtraArgError) as exc_info:
            build_command(offending, port=8081)

        message = str(exc_info.value)
        assert "other-model" in message
        assert "qwen3.8-27b" not in message


class TestStartServer:
    """Tests for start_server function."""

    def test_normal_start(self, minimal_config):
        """Normal successful server start.

        Patches ``log_rotation.rotate_if_needed`` to a no-op so the
        MagicMock ``LOG_DIR`` doesn't break the new ADR-LLNCH-013 rotation
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


class TestProcessScanCache:
    """Tests for the issue #392 TTL cache fronting the process-table scans.

    ``_reset_process_scan_cache`` (autouse, tests/conftest.py) purges the
    cache before and after every test, so each test here starts cold.
    """

    def test_repeated_calls_within_ttl_hit_cache(self):
        """Two calls within the TTL window scan psutil.process_iter once."""
        mock_proc = MagicMock()
        mock_proc.name.return_value = "llama-server"
        mock_proc.cmdline.return_value = ["llama-server", "--port", "8080"]

        with patch("psutil.process_iter", return_value=[mock_proc]) as mock_iter:
            first = find_all_llama_servers()
            second = find_all_llama_servers()

            assert mock_iter.call_count == 1
            assert first == second == [mock_proc]

    def test_call_after_ttl_rescans(self):
        """A call after the TTL has elapsed triggers a fresh scan."""
        mock_proc = MagicMock()
        mock_proc.name.return_value = "llama-server"
        mock_proc.cmdline.return_value = ["llama-server", "--port", "8080"]

        fake_time = [1000.0]
        with patch("psutil.process_iter", return_value=[mock_proc]) as mock_iter, \
             patch("time.monotonic", side_effect=lambda: fake_time[0]):
            find_all_llama_servers()
            assert mock_iter.call_count == 1

            fake_time[0] += 3.1  # past the 3s TTL
            find_all_llama_servers()
            assert mock_iter.call_count == 2

    def test_scan_functions_have_independent_cache_keys(self):
        """Calling one scan function must not serve the other's cached result."""
        mock_proc = MagicMock()
        mock_proc.name.return_value = "llama-server"
        mock_proc.cmdline.return_value = ["llama-server", "--port", "8080"]

        with patch("psutil.process_iter", return_value=[mock_proc]) as mock_iter:
            plain = find_all_llama_servers()
            discovered = discover_all()

            # Distinct shapes: bare Process list vs list[ServerProcessInfo].
            assert plain == [mock_proc]
            assert discovered == [
                ServerProcessInfo(
                    pid=mock_proc.pid, port=8080, alias=None,
                    model_path=None, create_time=mock_proc.create_time(),
                    cmdline_unreadable=False,
                )
            ]
            # Each populated its own cache slot rather than reusing the
            # other's — two independent scans, not one shared hit.
            assert mock_iter.call_count == 2

    def test_invalidate_forces_rescan_within_ttl(self):
        """invalidate_process_scan_cache() forces a rescan even inside the TTL."""
        mock_proc = MagicMock()
        mock_proc.name.return_value = "llama-server"
        mock_proc.cmdline.return_value = ["llama-server", "--port", "8080"]

        with patch("psutil.process_iter", return_value=[mock_proc]) as mock_iter:
            find_all_llama_servers()
            assert mock_iter.call_count == 1

            invalidate_process_scan_cache()
            find_all_llama_servers()
            assert mock_iter.call_count == 2

    def test_start_server_invalidates_cache(self, minimal_config):
        """Issue #402: start_server() purges the scan cache intrinsically.

        Before the fix, only state.py's legacy path called
        invalidate_process_scan_cache() after a start; operations/start.py
        and operations/swap.py (the only live orchestration paths) never
        did, so a status read taken right after a start could still serve
        a pre-spawn cached scan for up to the 3s TTL. Invalidation now
        lives on the primitive itself, so any caller — including a future
        one — gets it for free.
        """
        mock_process = MagicMock()
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        with patch("llauncher.core.process.DEFAULT_SERVER_BINARY", mock_bin), \
             patch("subprocess.Popen", return_value=mock_process), \
             patch("llauncher.core.process.LOG_DIR") as mock_log_dir, \
             patch(
                 "llauncher.core.process.log_rotation.rotate_if_needed",
                 return_value=False,
             ), \
             patch("psutil.process_iter", return_value=[]) as mock_iter:
            mock_log_dir.mkdir = MagicMock()

            # Warm the cache before the start.
            find_all_llama_servers()
            assert mock_iter.call_count == 1

            start_server(minimal_config, port=8080)

            # A scan immediately after start() must not be served from
            # the pre-spawn cached result.
            find_all_llama_servers()
            assert mock_iter.call_count == 2

    def test_stop_server_by_pid_invalidates_cache_on_success(self):
        """Issue #402: stop_server_by_pid() purges the cache when it
        actually terminates a process — the same intrinsic-invalidation
        contract as start_server(), covering the reliably-observable stop
        path called out in the issue (``/stop`` returns before the scan
        cache would otherwise naturally expire).
        """
        mock_proc = MagicMock()
        mock_proc.children.return_value = []

        with patch("psutil.Process", return_value=mock_proc), \
             patch("psutil.process_iter", return_value=[]) as mock_iter:
            find_all_llama_servers()
            assert mock_iter.call_count == 1

            result = stop_server_by_pid(12345)
            assert result is True

            find_all_llama_servers()
            assert mock_iter.call_count == 2

    def test_stop_server_by_pid_leaves_cache_alone_when_nothing_stopped(self):
        """A no-op stop (process already gone) has nothing to invalidate."""
        with patch(
            "psutil.Process", side_effect=psutil.NoSuchProcess(12345, None)
        ), patch("psutil.process_iter", return_value=[]) as mock_iter:
            find_all_llama_servers()
            assert mock_iter.call_count == 1

            result = stop_server_by_pid(12345)
            assert result is False

            # Still within the TTL and nothing changed — cache hit.
            find_all_llama_servers()
            assert mock_iter.call_count == 1


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


class TestFindAvailablePortEdgeCases:
    """Additional tests for find_available_port edge cases.

    Renamed from ``TestFindAvailablePort`` (#coverage close-out): the
    duplicate class name shadowed the canonical ``TestFindAvailablePort``
    above at import time, so its six tests — including the
    preferred-port-available (line 66) and default-start (line 61) cases
    — were silently never collected. Renaming revives both classes.
    """

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

    def test_wait_for_server_ready_dead_process_fast_fails(self):
        """#368: a process that has already exited short-circuits the poll.

        Without a liveness check the loop would burn the entire
        ``timeout`` ceiling polling a port/log a dead process can never
        produce. With ``process`` wired in, the very first tick sees
        ``poll()`` return a non-None exit code and returns immediately —
        proven here by a ``timeout`` far longer than the wall time the
        test tolerates, with ``time.sleep`` intentionally left real so a
        regression (falling through to the full poll loop) would make
        this test time out rather than silently pass.
        """
        from llauncher.core.process import wait_for_server_ready

        dead_process = MagicMock()
        dead_process.poll.return_value = 1  # exited, nonzero rc

        def mock_socket(*args, **kwargs):
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 1  # port never opens
            mock_sock.settimeout = MagicMock()
            mock_sock.close = MagicMock()
            return mock_sock

        with patch("llauncher.core.process.find_server_by_port", return_value=None):
            with patch("socket.socket", side_effect=mock_socket):
                is_ready, logs = wait_for_server_ready(
                    8080, timeout=60, check_interval=0.05, process=dead_process
                )

        assert is_ready is False
        assert logs == []
        dead_process.poll.assert_called()

    def test_wait_for_server_ready_live_process_still_polls_normally(self):
        """A live ``process`` (``poll()`` returns None) doesn't short-circuit."""
        from llauncher.core.process import wait_for_server_ready

        live_process = MagicMock()
        live_process.poll.return_value = None  # still running

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        def mock_socket(*args, **kwargs):
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0  # port open
            mock_sock.settimeout = MagicMock()
            mock_sock.close = MagicMock()
            return mock_sock

        with patch("llauncher.core.process.find_server_by_port", return_value=mock_proc):
            with patch("llauncher.core.process.stream_logs", return_value=["server started and listening"]):
                with patch("socket.socket", side_effect=mock_socket):
                    with patch("time.sleep"):  # Skip actual sleep
                        is_ready, logs = wait_for_server_ready(
                            8080, timeout=2, check_interval=0.1, process=live_process
                        )

        assert is_ready is True
        assert logs == ["server started and listening"]


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

        Per ADR-LLNCH-013, ``_tail_file`` reads bytes and decodes with
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
    """ADR-LLNCH-013 bounded-tail tests — _tail_file must not slurp the whole file."""

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
    """ADR-LLNCH-013 — start_server uses append mode, writes a banner, rotates first."""

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

    def test_banner_is_utc_wallclock_anchor_with_canonical_name(
        self, tmp_path, minimal_config
    ):
        """Issue #405 — the banner anchors the log to the wall clock.

        llama-server's own log lines carry only time-since-start offsets,
        so the banner must stamp an *absolute UTC* timestamp plus the
        canonical model name (``ModelConfig.name``, the mint — same
        identity as the ``--alias`` emission) and the port. That header is
        what lets every relative offset in the log join to the audit
        ledger's UTC times.
        """
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log_file = log_dir / f"{log_stem_for('test-model')}-8081.log"

        before = datetime.now(timezone.utc)
        with patch("llauncher.core.process.DEFAULT_SERVER_BINARY", mock_bin), \
             patch("llauncher.core.process.LOG_DIR", log_dir), \
             patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            start_server(minimal_config, port=8081)
        after = datetime.now(timezone.utc)

        first_line = log_file.read_text(encoding="utf-8").splitlines()[0]
        match = re.fullmatch(
            r"=== started at (?P<ts>\S+) port=(?P<port>\d+)"
            r" model=(?P<model>.+) ===",
            first_line,
        )
        assert match, f"banner line malformed: {first_line!r}"

        # Timestamp is ISO-8601, timezone-aware, and explicitly UTC.
        ts = datetime.fromisoformat(match["ts"])
        assert ts.tzinfo is not None, "banner timestamp is naive (no tz)"
        assert ts.utcoffset() == timedelta(0), (
            f"banner timestamp not UTC: {match['ts']!r}"
        )
        # And it is the actual spawn time, not a constant.
        assert before <= ts <= after

        assert match["port"] == "8081"
        # Canonical name from the mint, not a sanitized/derived string.
        assert match["model"] == minimal_config.name == "test-model"

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


class TestBuildCommandManagedFlagDriftGuard:
    """ADR-026 / issue #477: pin the exact, deliberately-small set of
    natively-emitted flags that fall outside ``DENIED_EXTRA_ARG_FLAGS``.

    Per the #477 ratification (point 5), the deny-list covers only the
    identity/security/observability flags (``--alias``, ``-m``/``--model``,
    ``--host``/``--port``, ``--api-key``, ``--metrics``,
    ``--slots``/``--no-slots``) — *not* the remaining llauncher-owned
    fields (``--mmproj``, ``--n-gpu-layers``, ``-c``, ``--parallel``). A
    duplicate of one of those four in ``extra_args`` is not guarded against
    at the door; this test exists so that scope is a pinned, intentional
    fact, not an accident a future edit could silently widen or narrow.
    """

    @staticmethod
    def _is_flag(token: str) -> bool:
        # A flag token starts with '-' and is not a (possibly negative) number.
        if not token.startswith("-"):
            return False
        try:
            float(token)
        except ValueError:
            return True
        return False

    # The natively-emitted flags NOT covered by DENIED_EXTRA_ARG_FLAGS,
    # per the ratification's deliberate scope (point 5).
    _UNGUARDED_OWNED_FLAGS = frozenset({"--mmproj", "--n-gpu-layers", "-c", "--parallel"})

    def test_natively_emitted_flags_match_the_ratified_scope(self) -> None:
        # Exercise every conditional branch in build_command so all native
        # flags are emitted. extra_args is empty so only native flags appear.
        cfg = ModelConfig.from_dict_unvalidated({
            "name": "drift-model",
            "model_path": "/fake/model.gguf",
            "mmproj_path": "/fake/mmproj.gguf",
            "n_gpu_layers": 10,
            "ctx_size": 4096,
            "parallel": 4,
            "metrics": True,
            "slots": True,
            "extra_args": "",
        })
        cmd = build_command(cfg, port=8080, host="0.0.0.0")

        emitted_flags = {t for t in cmd if self._is_flag(t)}
        registered = DENIED_EXTRA_ARG_FLAGS | self._UNGUARDED_OWNED_FLAGS

        unregistered = emitted_flags - registered
        assert not unregistered, (
            "build_command emits native flags not registered in "
            f"DENIED_EXTRA_ARG_FLAGS or the pinned _UNGUARDED_OWNED_FLAGS "
            f"set: {sorted(unregistered)}. If this is a new owned field, "
            "add it to one of the two sets deliberately (ADR-026 / #477)."
        )


class TestVerifyPid:
    """Issue #466 Phase 1: ``verify_pid`` — pid-addressed lookup.

    One row per outcome in the ratified plan's §3 contract table. Each
    test patches ``psutil.Process`` (module-attribute style, mirroring
    ``test_lockfile.py``'s ``is_pid_alive`` tests) rather than the real
    process table, so these run deterministically on any host.
    """

    def test_dead_pid_returns_none(self, monkeypatch):
        from llauncher.core import process as proc

        def _raise_no_such_process(_pid):
            raise psutil.NoSuchProcess(_pid)

        monkeypatch.setattr(proc.psutil, "Process", _raise_no_such_process)
        assert proc.verify_pid(4242) is None

    def test_zombie_status_returns_none(self, monkeypatch):
        from llauncher.core import process as proc

        p = MagicMock()
        p.is_running.return_value = True
        p.status.return_value = psutil.STATUS_ZOMBIE

        monkeypatch.setattr(proc.psutil, "Process", lambda pid: p)
        assert proc.verify_pid(4242) is None

    def test_zombie_process_exception_at_construction_returns_none(self, monkeypatch):
        """``psutil.ZombieProcess`` subclasses ``NoSuchProcess`` — same branch."""
        from llauncher.core import process as proc

        def _raise_zombie(_pid):
            raise psutil.ZombieProcess(_pid)

        monkeypatch.setattr(proc.psutil, "Process", _raise_zombie)
        assert proc.verify_pid(4242) is None

    def test_no_such_process_race_on_cmdline_returns_none(self, monkeypatch):
        """A process that vanishes between liveness check and cmdline read."""
        from llauncher.core import process as proc

        p = MagicMock()
        p.is_running.return_value = True
        p.status.return_value = psutil.STATUS_RUNNING
        p.cmdline.side_effect = psutil.NoSuchProcess(4242)

        monkeypatch.setattr(proc.psutil, "Process", lambda pid: p)
        assert proc.verify_pid(4242) is None

    def test_access_denied_on_construction_yields_unreadable_info(self, monkeypatch):
        """#208: present-but-inaccessible must not be dropped as ``None``."""
        from llauncher.core import process as proc

        def _raise_access_denied(_pid):
            raise psutil.AccessDenied(pid=_pid)

        monkeypatch.setattr(proc.psutil, "Process", _raise_access_denied)
        info = proc.verify_pid(4242)

        assert info is not None
        assert info.pid == 4242
        assert info.port is None
        assert info.alias is None
        assert info.model_path is None
        assert info.cmdline_unreadable is True

    def test_access_denied_on_cmdline_yields_unreadable_info(self, monkeypatch):
        """#208: liveness readable, argv is not — still unknown-alive."""
        from llauncher.core import process as proc

        p = MagicMock()
        p.is_running.return_value = True
        p.status.return_value = psutil.STATUS_RUNNING
        p.cmdline.side_effect = psutil.AccessDenied(pid=4242)

        monkeypatch.setattr(proc.psutil, "Process", lambda pid: p)
        info = proc.verify_pid(4242)

        assert info is not None
        assert info.cmdline_unreadable is True
        assert info.port is None

    def test_live_non_llama_process_returns_none(self, monkeypatch):
        """PID-reuse defense: alive + readable but not a llama-server."""
        from llauncher.core import process as proc

        p = MagicMock()
        p.is_running.return_value = True
        p.status.return_value = psutil.STATUS_RUNNING
        p.cmdline.return_value = ["nginx", "-c", "/etc/nginx.conf"]
        p.name.return_value = "nginx"

        monkeypatch.setattr(proc.psutil, "Process", lambda pid: p)
        assert proc.verify_pid(4242) is None

    def test_expect_port_mismatch_returns_none_and_warns(self, monkeypatch, caplog):
        """ADR-LLNCH-008: argv port disagrees with the lockfile's claim → refuse."""
        from llauncher.core import process as proc

        p = MagicMock()
        p.is_running.return_value = True
        p.status.return_value = psutil.STATUS_RUNNING
        p.cmdline.return_value = ["llama-server", "--port", "8081", "-m", "/a.gguf"]
        p.name.return_value = "llama-server"

        monkeypatch.setattr(proc.psutil, "Process", lambda pid: p)

        with caplog.at_level(logging.WARNING, logger="llauncher.core.process"):
            info = proc.verify_pid(4242, expect_port=9999)

        assert info is None
        assert any("4242" in r.getMessage() for r in caplog.records)

    def test_live_llama_server_returns_full_info(self, monkeypatch):
        """The good case: alias/port/model_path/create_time populated."""
        from llauncher.core import process as proc

        p = MagicMock()
        p.is_running.return_value = True
        p.status.return_value = psutil.STATUS_RUNNING
        p.cmdline.return_value = [
            "llama-server", "--port", "8081", "--alias", "my-model",
            "-m", "/models/a.gguf",
        ]
        p.name.return_value = "llama-server"
        p.create_time.return_value = 1234567890.5

        monkeypatch.setattr(proc.psutil, "Process", lambda pid: p)
        info = proc.verify_pid(4242, expect_port=8081)

        assert info == proc.ServerProcessInfo(
            pid=4242,
            port=8081,
            alias="my-model",
            model_path="/models/a.gguf",
            create_time=1234567890.5,
            cmdline_unreadable=False,
        )

    def test_live_llama_server_no_expect_port_still_verifies(self, monkeypatch):
        """``expect_port=None`` (the discovery-less case) skips the port gate."""
        from llauncher.core import process as proc

        p = MagicMock()
        p.is_running.return_value = True
        p.status.return_value = psutil.STATUS_RUNNING
        p.cmdline.return_value = ["llama-server", "--port", "8082"]
        p.name.return_value = "llama-server"
        p.create_time.return_value = 999.0

        monkeypatch.setattr(proc.psutil, "Process", lambda pid: p)
        info = proc.verify_pid(4242)

        assert info.port == 8082
        assert info.alias is None
        assert info.model_path is None

    def test_create_time_access_denied_falls_back_to_none(self, monkeypatch):
        """A late ``AccessDenied`` on ``create_time()`` degrades gracefully."""
        from llauncher.core import process as proc

        p = MagicMock()
        p.is_running.return_value = True
        p.status.return_value = psutil.STATUS_RUNNING
        p.cmdline.return_value = ["llama-server", "--port", "8081"]
        p.name.return_value = "llama-server"
        p.create_time.side_effect = psutil.AccessDenied(pid=4242)

        monkeypatch.setattr(proc.psutil, "Process", lambda pid: p)
        info = proc.verify_pid(4242)

        assert info is not None
        assert info.create_time is None


class TestDiscoverAllCreateTimeGuard:
    """Issue #466 Phase 1: ``discover_all``'s late ``create_time()`` guard.

    Mirrors ``TestVerifyPid::test_create_time_access_denied_falls_back_to_none``
    for the process-table walk: a process that exits (or becomes unreadable)
    between the argv read and the ``create_time()`` read must still be
    reported with ``create_time=None`` rather than silently dropped from
    the orphan roster.
    """

    def test_create_time_access_denied_still_yields_info(self):
        """``AccessDenied`` mid-walk degrades to ``create_time=None``."""
        p = MagicMock()
        p.pid = 4242
        p.name.return_value = "llama-server"
        p.cmdline.return_value = [
            "llama-server", "--port", "8081", "--alias", "my-model",
            "-m", "/models/a.gguf",
        ]
        p.create_time.side_effect = psutil.AccessDenied(pid=4242)

        with patch("psutil.process_iter", return_value=[p]):
            discovered = discover_all()

        assert discovered == [
            ServerProcessInfo(
                pid=4242,
                port=8081,
                alias="my-model",
                model_path="/models/a.gguf",
                create_time=None,
                cmdline_unreadable=False,
            )
        ]

    def test_create_time_zombie_still_yields_info(self):
        """A mid-walk ``ZombieProcess`` is the same degrade, not a drop."""
        p = MagicMock()
        p.pid = 99
        p.name.return_value = "llama-server"
        p.cmdline.return_value = ["llama-server", "--port", "8082"]
        p.create_time.side_effect = psutil.ZombieProcess(pid=99)

        with patch("psutil.process_iter", return_value=[p]):
            discovered = discover_all()

        assert len(discovered) == 1
        assert discovered[0].pid == 99
        assert discovered[0].create_time is None


class TestBuildCommandMalformedExtraArgs:
    """Unparseable ``extra_args`` quoting is a typed launch failure.

    ADR-026 removed every pydantic check from ``extra_args``, so the UI
    textarea, the MCP tool and the CLI all accept an unbalanced quote and
    persist it. ``build_command`` is the first and only place it is
    tokenized — ``shlex.split``'s bare ``ValueError`` there would escape
    ``operations.start`` / ``operations.swap``, whose except-tuple catches
    launch failures by type. Both ``extra_args`` failure modes therefore
    share one base class, and the operations layer catches the base.
    """

    def test_unbalanced_quote_raises_malformed_extra_args_error(
        self, minimal_config
    ) -> None:
        minimal_config.extra_args = '--chat-template "hello'
        with pytest.raises(MalformedExtraArgsError) as excinfo:
            build_command(minimal_config, port=8080)
        assert "test-model" in str(excinfo.value)

    def test_both_failure_modes_share_one_catchable_base(self) -> None:
        assert issubclass(MalformedExtraArgsError, ExtraArgsError)
        assert issubclass(DeniedExtraArgError, ExtraArgsError)
        assert issubclass(ExtraArgsError, ValueError)

    def test_operations_catch_the_base_class_not_a_subclass(self) -> None:
        """A regression guard for the actual defect: catching only

        ``DeniedExtraArgError`` let a malformed-quoting ``ValueError``
        through, because a sibling subclass is not caught by its sibling.
        """
        assert not issubclass(MalformedExtraArgsError, DeniedExtraArgError)
        for module in ("llauncher.operations.start", "llauncher.operations.swap"):
            source = Path(
                importlib.import_module(module).__file__
            ).read_text(encoding="utf-8")
            assert "proc.ExtraArgsError" in source, module
            assert "proc.DeniedExtraArgError" not in source, module

    def test_start_server_surfaces_it_before_popen(self, minimal_config) -> None:
        """No argv reaches ``subprocess.Popen`` when extra_args cannot parse."""
        minimal_config.extra_args = "--grammar-file 'unterminated"
        mock_bin = MagicMock()
        mock_bin.exists.return_value = True
        with patch("llauncher.core.process.DEFAULT_SERVER_BINARY", mock_bin), \
             patch("subprocess.Popen") as popen:
            with pytest.raises(MalformedExtraArgsError):
                start_server(minimal_config, 8080)
        popen.assert_not_called()
