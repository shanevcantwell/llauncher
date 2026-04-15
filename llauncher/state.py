"""State management for llauncher."""

import subprocess
import psutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from llauncher.core.config import ConfigStore
from llauncher.core.process import (
    DEFAULT_SERVER_BINARY,
    find_all_llama_servers,
    find_server_by_port,
    find_available_port,
    is_port_in_use,
    start_server as process_start_server,
    stop_server_by_port as process_stop_server,
)
from llauncher.models.config import (
    AuditEntry,
    ChangeRules,
    ModelConfig,
    RunningServer,
)


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

    def start_with_eviction(
        self,
        model_name: str,
        port: int,
        caller: str = "unknown",
        server_bin: Path = DEFAULT_SERVER_BINARY,
    ) -> tuple[bool, str]:
        """Start a server, stopping any existing server on the target port first.

        For local nodes only. Remote eviction requires agent-side coordination.

        Args:
            model_name: Name of the model to start.
            port: Port to use (will evict if already in use).
            caller: Name of the caller.
            server_bin: Path to llama-server binary.

        Returns:
            Tuple of (success, message).
        """
        if model_name not in self.models:
            self.record_action("start", model_name, caller, "error", "Model not found")
            return False, f"Model not found: {model_name}"

        config = self.models[model_name]

        # Track if eviction will happen (before modifying state)
        port_was_occupied = port in self.running

        # Stop any existing server on this port
        if port_was_occupied:
            existing_model = self.running[port].config_name
            stop_success, stop_msg = self.stop_server(port, caller)
            if not stop_success:
                self.record_action("evict", model_name, caller, "error",
                                 f"Failed to stop existing server: {stop_msg}")
                return False, f"Cannot evict: failed to stop {existing_model} on port {port}: {stop_msg}"
            self.record_action("evict", model_name, caller, "success",
                             f"Stopped {existing_model} on port {port}")

        # Validate the port is free now
        valid, msg = self.can_start(config, caller, port)
        if not valid:
            self.record_action("start", model_name, caller, "validation_error", msg)
            return False, msg

        # Start the process
        try:
            process = process_start_server(config, port, server_bin=server_bin)
            self.running[port] = RunningServer(
                pid=process.pid,
                port=port,
                config_name=model_name,
                start_time=datetime.now(),
            )
            self.record_action("start", model_name, caller, "success", f"Started on port {port}")
            if port_was_occupied:
                return True, f"Started {model_name} on port {port} (evicted previous server)"
            else:
                return True, f"Started {model_name} on port {port}"
        except Exception as e:
            self.record_action("start", model_name, caller, "error", str(e))
            return False, f"Failed to start: {e}"

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
