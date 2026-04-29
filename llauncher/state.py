"""State management for llauncher."""

import subprocess
import psutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from llauncher.core.config import ConfigStore
from llauncher.core.model_health import check_model_health, ModelHealthResult
from llauncher.core.process import (
    DEFAULT_SERVER_BINARY,
    find_all_llama_servers,
    find_server_by_port,
    find_available_port,
    is_port_in_use,
    start_server as process_start_server,
    stop_server_by_pid,
    stop_server_by_port as process_stop_server,
    wait_for_server_ready,
)
from llauncher.models.config import (
    AuditEntry,
    ChangeRules,
    ModelConfig,
    RunningServer,
)


@dataclass
class EvictionResult:
    """Result of an eviction-and-start operation.

    Provides structured information about what happened during a
    start-with-eviction flow, including rollback state and diagnostic logs.
    """

    success: bool
    port_state: str          # "unchanged" | "restored" | "serving" | "unavailable"
    error: str
    rolled_back: bool = False
    restored_model: str = ""
    previous_model: str = ""
    new_model_attempted: str = ""
    startup_logs: list[str] = field(default_factory=list)


@dataclass
class LauncherState:
    """Manages state across MCP tool calls and UI sessions.

    Tracks:
    - Model configurations (loaded from config + discovered scripts)
    - Running server processes (port → server info)
    - Audit log of actions
    - Change rules for validation
    """

    models: dict[str, ModelConfig] = field(default_factory=dict)
    running: dict[int, RunningServer] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)
    rules: ChangeRules = field(default_factory=ChangeRules)

    def __post_init__(self):
        """Initialize state on creation."""
        self.refresh()

    def refresh(self) -> None:
        """Refresh state from disk and process list."""
        # Load configurations from config.json (single source of truth)
        self.models = ConfigStore.load()

        # Refresh running servers
        self.refresh_running_servers()

    def refresh_running_servers(self) -> None:
        """Refresh the list of running servers from the process table."""
        current_running = {}

        for proc in find_all_llama_servers():
            try:
                cmdline = proc.cmdline()
                if not cmdline:
                    continue

                # Extract port from command line
                port = None
                model_path = None

                for i, arg in enumerate(cmdline):
                    if arg == "--port" and i + 1 < len(cmdline):
                        port = int(cmdline[i + 1])
                    elif arg == "-m" and i + 1 < len(cmdline):
                        model_path = cmdline[i + 1]

                if port:
                    # Find matching model config
                    config_name = self._find_model_by_path(model_path)

                    current_running[port] = RunningServer(
                        pid=proc.pid,
                        port=port,
                        config_name=config_name or "unknown",
                        start_time=datetime.now(),  # We don't track actual start time
                    )

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        self.running = current_running

    def _find_model_by_path(self, model_path: str | None) -> str | None:
        """Find model name by model path."""
        if not model_path:
            return None

        for name, config in self.models.items():
            if config.model_path == model_path:
                return name

        return None

    def can_start(
        self, config: ModelConfig, caller: str = "unknown", port: int | None = None
    ) -> tuple[bool, str]:
        """Validate if a model can be started.

        Checks:
        - Port is not in use
        - Port is not blacklisted
        - Caller is not blacklisted
        - Model path exists

        Args:
            config: Model configuration to validate.
            caller: Name of the caller (e.g., "mcp", "ui", "cli").
            port: Optional specific port to check (uses default_port if not provided).

        Returns:
            Tuple of (is_valid, error_message).
        """
        # Determine which port to check
        check_port = port or config.default_port

        # If we have a specific port to check, validate it
        if check_port is not None:
            # Check if port is already in use by another model
            if check_port in self.running:
                return False, f"Port {check_port} is already in use by {self.running[check_port].config_name}"

            # Check if port is in use by any process
            if is_port_in_use(check_port):
                return False, f"Port {check_port} is already in use"

        # Check change rules (pass the port we're checking)
        valid, msg = self.rules.validate_start(config, caller, check_port)
        if not valid:
            return False, msg

        # Verify model path exists
        if not Path(config.model_path).exists():
            return False, f"Model path does not exist: {config.model_path}"

        return True, "OK"

    def can_stop(self, port: int, caller: str = "unknown") -> tuple[bool, str]:
        """Validate if a server can be stopped.

        Checks:
        - Server is running on that port
        - Caller is not blacklisted

        Args:
            port: Port of the server to stop.
            caller: Name of the caller.

        Returns:
            Tuple of (is_valid, error_message).
        """
        if port not in self.running:
            return False, f"No server running on port {port}"

        # Check change rules
        valid, msg = self.rules.validate_stop(port, caller)
        if not valid:
            return False, msg

        return True, "OK"

    def start_server(
        self,
        model_name: str,
        caller: str = "unknown",
        port: int | None = None,
        server_bin: Path = DEFAULT_SERVER_BINARY,
    ) -> tuple[bool, str, subprocess.Popen | None]:
        """Start a server for the given model.

        Args:
            model_name: Name of the model to start.
            caller: Name of the caller.
            port: Optional port override. If not provided, uses config.default_port
                  or auto-allocates from available ports.
            server_bin: Path to llama-server binary.

        Returns:
            Tuple of (success, message, process).
        """
        if model_name not in self.models:
            self.record_action("start", model_name, caller, "error", "Model not found")
            return False, f"Model not found: {model_name}", None

        config = self.models[model_name]

        # Resolve port: explicit override -> default_port -> auto-allocate
        preferred = port or config.default_port
        success, resolved_port, alloc_msg = find_available_port(preferred)
        if not success:
            self.record_action("start", model_name, caller, "error", alloc_msg)
            return False, f"Cannot allocate port: {alloc_msg}", None

        # Validate with the resolved port
        valid, msg = self.can_start(config, caller, resolved_port)
        if not valid:
            self.record_action("start", model_name, caller, "validation_error", msg)
            return False, msg, None

        # Pre-flight: check model file health (ADR-005)
        health = check_model_health(config.model_path)
        if not health.valid:
            detail = f"Model file unhealthy: {health.reason}".rstrip(".")
            self.record_action("start", model_name, caller, "validation_error",
                               f"Health check failed: {detail}")
            return False, f"Model path is invalid ({health.reason}): {config.model_path}", None

        # Start the process with resolved port
        try:
            process = process_start_server(config, resolved_port, server_bin=server_bin)

            # Update running state with resolved port
            self.running[resolved_port] = RunningServer(
                pid=process.pid,
                port=resolved_port,
                config_name=model_name,
                start_time=datetime.now(),
            )

            self.record_action("start", model_name, caller, "success", f"Started on port {resolved_port}")
            return True, f"Started {model_name} on port {resolved_port}", process

        except Exception as e:
            self.record_action("start", model_name, caller, "error", str(e))
            return False, f"Failed to start: {e}", None

    def stop_server(self, port: int, caller: str = "unknown") -> tuple[bool, str]:
        """Stop a server running on the given port.

        Args:
            port: Port of the server to stop.
            caller: Name of the caller.

        Returns:
            Tuple of (success, message).
        """
        # Validate
        valid, msg = self.can_stop(port, caller)
        if not valid:
            existing_model = self.running.get(port)
            if existing_model:
                model = existing_model
            else:
                model = RunningServer(pid=0, port=port, config_name="unknown", start_time=datetime.now())
            self.record_action("stop", model.config_name, caller, "validation_error", msg)
            return False, msg

        # Stop the process
        success = process_stop_server(port)

        if success:
            model_name = self.running[port].config_name
            del self.running[port]
            self.record_action("stop", model_name, caller, "success", f"Stopped port {port}")
            return True, f"Stopped server on port {port}"
        else:
            self.record_action("stop", "unknown", caller, "error", "Process not found")
            return False, "Failed to stop server"

    def _start_with_eviction_impl(
        self,
        model_name: str,
        port: int,
        caller: str = "unknown",
        server_bin: Path = DEFAULT_SERVER_BINARY,
        readiness_timeout: int = 120,
        strict_rollback: bool = False,
    ) -> EvictionResult:
        """Start a server with eviction of any existing process on the target port.

        Implements a 5-phase decision tree with full rollback support:
        1. Pre-flight checks (no state changes)
        2. Stop old model (if port occupied)
        3. Start new model (with rollback on failure)
        4. Readiness poll (with rollback on timeout)
        5. Success or detailed failure with diagnostics

        Args:
            model_name: Name of the model to start.
            port: Port to use (will evict if already in use). Must be between 1024-65535.
            caller: Name of the caller.
            server_bin: Path to llama-server binary.
            readiness_timeout: Seconds to wait for /status readiness after starting.
            strict_rollback: If True, requires old model config+path exist for rollback.

        Returns:
            EvictionResult with structured outcome information.
        """
        # ── Phase 1: Pre-flight (no state changes) ──────────────────────

        # 1. Look up model config
        if model_name not in self.models:
            self.record_action("start", model_name, caller, "error", "Model not found")
            return EvictionResult(
                success=False,
                port_state="unchanged",
                error=f"Model '{model_name}' not found in config",
            )

        config = self.models[model_name]

        # 2. Pre-flight health check (ADR-005) — replaces bare Path.exists()
        health = check_model_health(config.model_path)
        if not health.valid:
            self.record_action("start", model_name, caller, "validation_error",
                               f"Health check failed: {health.reason}")
            return EvictionResult(
                success=False,
                port_state="unchanged",
                error=f"Model path unhealthy ({health.reason}): {config.model_path}",
            )

        # 3. Check new model not already running elsewhere on a different port
        for existing_port, srv in self.running.items():
            if srv.config_name == model_name and existing_port != port:
                return EvictionResult(
                    success=False,
                    port_state="unchanged",
                    error=f"Model '{model_name}' is already running on port {existing_port}",
                )

        # 4-5. If port occupied, check old config exists and capture previous_model
        previous_model = ""
        if port in self.running:
            previous_model = self.running[port].config_name
            if strict_rollback and previous_model and previous_model not in self.models:
                self.record_action("evict", model_name, caller, "error",
                                   f"Cannot evict: no config for existing model '{previous_model}'")
                return EvictionResult(
                    success=False,
                    port_state="unchanged",
                    error=f"Cannot evict: no config for running model '{previous_model}'",
                    previous_model=previous_model,
                )
            if strict_rollback and previous_model:
                old_config = self.models[previous_model]
                if not Path(old_config.model_path).exists():
                    self.record_action("evict", model_name, caller, "error",
                                       f"Cannot evict: old model path missing for '{previous_model}'")
                    return EvictionResult(
                        success=False,
                        port_state="unchanged",
                        error=f"Cannot evict: model path missing for '{previous_model}'",
                        previous_model=previous_model,
                    )

        # Validate port range
        if port < 1024 or port > 65535:
            self.record_action("start", model_name, caller, "error",
                               f"Invalid port {port}: must be between 1024-65535")
            return EvictionResult(
                success=False,
                port_state="unchanged",
                error=f"Invalid port: {port}. Must be between 1024-65535.",
            )

        # ── Phase 2: Stop old model (if port occupied) ──────────────────

        new_started = False
        new_pid = 0

        if port in self.running:
            stop_success, stop_msg = self.stop_server(port, caller)
            if not stop_success:
                self.record_action("evict", model_name, caller, "error",
                                   f"Failed to stop existing server: {stop_msg}")
                return EvictionResult(
                    success=False,
                    port_state="unchanged",
                    error=f"Cannot evict: Failed to stop existing server on port {port}",
                    previous_model=previous_model,
                )
            self.record_action("evict", model_name, caller, "success",
                               f"Stopped {previous_model} on port {port}")

        # ── Phase 3: Start new model ────────────────────────────────────

        start_exception = None
        try:
            process = process_start_server(config, port, server_bin=server_bin)
            new_pid = process.pid
            self.running[port] = RunningServer(
                pid=process.pid,
                port=port,
                config_name=model_name,
                start_time=datetime.now(),
            )
            new_started = True
            self.record_action("start", model_name, caller, "success", f"Started on port {port}")
        except Exception as e:
            start_exception = e
            self.record_action("start", model_name, caller, "error", str(e))

        # Rollback logic (if start fails)
        if start_exception is not None and strict_rollback and previous_model and previous_model in self.models:
            old_config = self.models[previous_model]
            try:
                old_process = process_start_server(old_config, port, server_bin=server_bin)
                self.running[port] = RunningServer(
                    pid=old_process.pid,
                    port=port,
                    config_name=previous_model,
                    start_time=datetime.now(),
                )
                self.record_action("rollback", previous_model, caller, "success",
                                   f"Rolled back old server on port {port}")
                return EvictionResult(
                    success=False,
                    port_state="restored",
                    error=str(start_exception),
                    rolled_back=True,
                    restored_model=previous_model,
                    previous_model=previous_model,
                )
            except Exception:
                self.running.pop(port, None)
                return EvictionResult(
                    success=False,
                    port_state="unavailable",
                    error=f"Swap failed: {start_exception}. Rollback failed — manual intervention required.",
                    previous_model=previous_model,
                )

        if start_exception is not None:
            return EvictionResult(
                success=False,
                port_state="unavailable",
                error=f"Failed to start: {start_exception}",
                previous_model=previous_model,
                new_model_attempted=model_name,
            )

        # ── Phase 4: Readiness poll ─────────────────────────────────────

        try:
            ready = wait_for_server_ready(port, timeout=readiness_timeout)
            if not ready:
                # Terminate new process
                stop_server_by_pid(new_pid)
                self.running.pop(port, None)

                # Rollback logic on readiness failure
                if strict_rollback and previous_model and previous_model in self.models:
                    old_config = self.models[previous_model]
                    try:
                        old_process = process_start_server(old_config, port, server_bin=server_bin)
                        self.running[port] = RunningServer(
                            pid=old_process.pid,
                            port=port,
                            config_name=previous_model,
                            start_time=datetime.now(),
                        )
                        self.record_action("rollback", previous_model, caller, "success",
                                           f"Rolled back old server on port {port} (readiness failure)")
                        return EvictionResult(
                            success=False,
                            port_state="restored",
                            error=f"Readiness timeout after {readiness_timeout}s.",
                            rolled_back=True,
                            restored_model=previous_model,
                            previous_model=previous_model,
                        )
                    except Exception:
                        self.running.pop(port, None)
                        return EvictionResult(
                            success=False,
                            port_state="unavailable",
                            error=f"Readiness timeout after {readiness_timeout}s. Rollback failed — manual intervention required.",
                            previous_model=previous_model,
                            new_model_attempted=model_name,
                        )

                return EvictionResult(
                    success=False,
                    port_state="unavailable",
                    error=f"Readiness timeout after {readiness_timeout}s.",
                    previous_model=previous_model,
                    new_model_attempted=model_name,
                )
        except Exception as e:
            # wait_for_server_ready itself raised
            stop_server_by_pid(new_pid)
            self.running.pop(port, None)

            if strict_rollback and previous_model and previous_model in self.models:
                old_config = self.models[previous_model]
                try:
                    old_process = process_start_server(old_config, port, server_bin=server_bin)
                    self.running[port] = RunningServer(
                        pid=old_process.pid,
                        port=port,
                        config_name=previous_model,
                        start_time=datetime.now(),
                    )
                    self.record_action("rollback", previous_model, caller, "success",
                                       f"Rolled back old server on port {port} (readiness error)")
                    return EvictionResult(
                        success=False,
                        port_state="restored",
                        error=f"Readiness check failed: {e}",
                        rolled_back=True,
                        restored_model=previous_model,
                        previous_model=previous_model,
                    )
                except Exception:
                    self.running.pop(port, None)
                    return EvictionResult(
                        success=False,
                        port_state="unavailable",
                        error=f"Readiness check failed: {e}. Rollback failed — manual intervention required.",
                        previous_model=previous_model,
                        new_model_attempted=model_name,
                    )

            return EvictionResult(
                success=False,
                port_state="unavailable",
                error=f"Readiness check failed: {e}",
                previous_model=previous_model,
                new_model_attempted=model_name,
            )

        # ── Phase 5: Success ────────────────────────────────────────────

        self.refresh_running_servers()
        return EvictionResult(
            success=True,
            port_state="serving",
            error="",
            new_model_attempted=model_name,
            previous_model=previous_model,
        )

    def start_with_eviction_compat(
        self,
        model_name: str,
        port: int,
        caller: str = "unknown",
        server_bin: Path = DEFAULT_SERVER_BINARY,
    ) -> tuple[bool, str]:
        """Backward-compatible wrapper returning (success, message).

        Calls _start_with_eviction_impl and converts the EvictionResult
        into the legacy tuple format expected by older callers.
        """
        result = self._start_with_eviction_impl(
            model_name, port, caller, server_bin,
            readiness_timeout=120, strict_rollback=False,
        )
        msg = result.error if not result.success else f"Started {result.new_model_attempted} on port {port}"
        if result.rolled_back:
            msg += f" — rolled back to {result.restored_model}"
        return result.success, msg

    start_with_eviction = start_with_eviction_compat  # legacy alias

    def record_action(
        self,
        action: str,
        model: str,
        caller: str,
        result: str,
        message: str | None = None,
    ) -> None:
        """Record an action in the audit log.

        Args:
            action: Action type (start, stop, update, etc.)
            model: Model name affected.
            caller: Who initiated the action.
            result: Result (success, error, validation_error)
            message: Optional details.
        """
        entry = AuditEntry(
            timestamp=datetime.now(),
            action=action,
            model=model,
            caller=caller,
            result=result,
            message=message,
        )
        self.audit.append(entry)

    def get_model_status(self, model_name: str) -> dict:
        """Get the current status of a model.

        Args:
            model_name: Name of the model.

        Returns:
            Dictionary with status information.
        """
        if model_name not in self.models:
            return {"status": "unknown", "message": "Model not found"}

        config = self.models[model_name]

        # Check if running
        for port, server in self.running.items():
            if server.config_name == model_name:
                return {
                    "status": "running",
                    "port": port,
                    "pid": server.pid,
                }

        return {
            "status": "stopped",
            "default_port": config.default_port,
        }
