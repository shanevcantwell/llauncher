"""State management for llauncher."""

import logging
import subprocess
import psutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from llauncher.core.config import ConfigStore
from llauncher.core.process import (
    DEFAULT_SERVER_BINARY,
    find_all_llama_servers,
    invalidate_process_scan_cache,
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
from llauncher.operations.orphan import (
    OrphanInfo,
    list_orphans,
    record_observed_orphan,
)

logger = logging.getLogger(__name__)


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
    orphans: list[OrphanInfo] = field(default_factory=list)
    # In-memory dedupe sets for ADR-015 reconciliation. Both are pruned
    # to the set of currently-observed pids on every refresh so that a
    # pid which leaves and later re-enters the scan re-emits exactly
    # once.
    _observed_orphan_pids: set[int] = field(default_factory=set)
    _warned_unreadable_pids: set[int] = field(default_factory=set)

    def __post_init__(self):
        """Initialize state on creation."""
        self.refresh()

    def refresh(self) -> None:
        """Refresh state from disk and process list."""
        # Load configurations from config.json (single source of truth)
        self.models = ConfigStore.load()

        # Refresh running servers
        self.refresh_running_servers()

        # Refresh orphan (unmanaged) llama-server processes per ADR-015.
        self.refresh_orphans()

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
                alias = None

                for i, arg in enumerate(cmdline):
                    if arg == "--port" and i + 1 < len(cmdline):
                        port = int(cmdline[i + 1])
                    elif arg == "-m" and i + 1 < len(cmdline):
                        model_path = cmdline[i + 1]
                    elif arg == "--alias" and i + 1 < len(cmdline):
                        alias = cmdline[i + 1]

                if port:
                    # Issue #423 (ONE-MINT / IDENTITY⊥ENVELOPE): the launched
                    # ``--alias`` IS the canonical config name (see
                    # process.build_command), so it is the identity source
                    # of truth. A path→config reverse lookup is ambiguous
                    # whenever two configs share one gguf and picks an
                    # arbitrary sibling. Only fall back to the path lookup
                    # for processes llauncher didn't launch (no ``--alias``
                    # in their cmdline, e.g. a foreign/orphan llama-server).
                    config_name = alias or self._find_model_by_path(model_path)

                    current_running[port] = RunningServer(
                        pid=proc.pid,
                        port=port,
                        config_name=config_name or "unknown",
                        start_time=datetime.now(),  # We don't track actual start time
                    )

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        self.running = current_running

    def refresh_orphans(self) -> None:
        """Refresh the list of orphan (unmanaged) llama-server processes.

        Per ADR-015, an orphan is a live ``llama-server`` whose port (or
        pid, when port-keyed) does not match a live lockfile. Audit
        emission cadence:

        - First sighting of an orphan pid → emit ``observed_orphan``.
        - Pid still present on subsequent refreshes → no re-emission.
        - Pid leaves the scan → drop from dedupe set; next sighting
          re-emits.

        Processes whose ``cmdline`` cannot be read (``psutil.AccessDenied``)
        are logged once per pid (deduped via
        :attr:`_warned_unreadable_pids`) and surfaced in
        :attr:`orphans` so the operator can still see them.
        """
        current = list_orphans()
        current_pids = {o.pid for o in current}

        for orphan in current:
            if orphan.cmdline_unreadable:
                if orphan.pid not in self._warned_unreadable_pids:
                    logger.warning(
                        "llama-server pid %d: cmdline unreadable "
                        "(permission denied); skipping reconciliation.",
                        orphan.pid,
                    )
                    self._warned_unreadable_pids.add(orphan.pid)
                # Don't emit audit for unreadable pids — we can't tell
                # if they're managed or not. Surface them in self.orphans
                # but skip the OBSERVED_ORPHAN entry to avoid noise.
                continue

            if orphan.pid in self._observed_orphan_pids:
                continue

            record_observed_orphan(orphan)
            self._observed_orphan_pids.add(orphan.pid)

        # Prune pids that left the scan so a future re-appearance emits
        # again. Use intersection rather than reassignment to preserve
        # the set's identity in case external code holds a reference.
        self._observed_orphan_pids &= current_pids
        self._warned_unreadable_pids &= current_pids

        self.orphans = current

    def _find_model_by_path(self, model_path: str | None) -> str | None:
        """Find model name by model path."""
        if not model_path:
            return None

        for name, config in self.models.items():
            if config.model_path == model_path:
                return name

        return None

    def can_start(
        self, config: ModelConfig, caller: str = "unknown", *, port: int
    ) -> tuple[bool, str]:
        """Validate if a model can be started on ``port``.

        Per ADR-010 / issue #58, ``port`` is required; there is no
        ``port=None`` skip-port-checks branch and no placeholder sentinel
        passed down to :meth:`ChangeRules.validate_start`. ``port`` is
        keyword-only to make pre-existing positional callers fail loudly
        rather than silently change semantics.

        Checks:

        - Port is not already used by another model in :attr:`running`.
        - Port is not in use by any process.
        - Caller / port pass :attr:`rules.validate_start`.
        - Model path exists.

        Returns:
            Tuple of ``(is_valid, error_message)``.
        """
        # Port collision against our own running registry.
        if port in self.running:
            return False, (
                f"Port {port} is already in use by "
                f"{self.running[port].config_name}"
            )

        # Port collision against any process on the host.
        if is_port_in_use(port):
            return False, f"Port {port} is already in use"

        # Change-rules validation (whitelists/blacklists for ports + callers).
        valid, msg = self.rules.validate_start(config, caller, port)
        if not valid:
            return False, msg

        # Model file must exist on disk.
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
        port: int,
        caller: str = "unknown",
        server_bin: Path = DEFAULT_SERVER_BINARY,
    ) -> tuple[bool, str, subprocess.Popen | None]:
        """Start a server for the given model on the specified port.

        Per ADR-010 the caller supplies ``port``; this method no longer
        auto-allocates (issue #58 / audit C3). v2 callers are routed
        through :mod:`llauncher.operations.start` instead; this legacy
        path is retained only for the few v1 tests pending M3 cleanup.

        Args:
            model_name: Name of the model to start.
            port: Port to bind to. Required (ADR-010); no env fallback.
            caller: Name of the caller (for audit log).
            server_bin: Path to llama-server binary.

        Returns:
            Tuple of (success, message, process).
        """
        if model_name not in self.models:
            self.record_action("start", model_name, caller, "error", "Model not found")
            return False, f"Model not found: {model_name}", None

        config = self.models[model_name]

        # Validate with the supplied port
        valid, msg = self.can_start(config, caller, port=port)
        if not valid:
            self.record_action("start", model_name, caller, "validation_error", msg)
            return False, msg, None

        # Pre-flight model-file health validation moved to the operations
        # layer per audit C2 / issue #57. Callers that go through
        # ``operations.start()`` get the ADR-005 check via the
        # ``model_health_check`` seam. This legacy state.start_server path
        # is kept only for the in-flight M1→M2 transition; it now skips the
        # health check, matching its M1 minimal contract.

        # Start the process with the supplied port
        try:
            process = process_start_server(config, port, server_bin=server_bin)

            # Update running state
            self.running[port] = RunningServer(
                pid=process.pid,
                port=port,
                config_name=model_name,
                start_time=datetime.now(),
            )
            # Issue #392: a just-performed start must be reflected on the
            # very next refresh(), not after the process-scan cache's TTL.
            invalidate_process_scan_cache()

            self.record_action("start", model_name, caller, "success", f"Started on port {port}")
            return True, f"Started {model_name} on port {port}", process

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
            # Issue #392: reflect the just-performed stop immediately.
            invalidate_process_scan_cache()
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

        # 2. Pre-flight model-file health check moved to the operations
        # layer (audit C2 / issue #57). UI callers now route eviction
        # through ``operations.swap()`` which runs the health check via its
        # ``model_health_check`` seam. This legacy method is retained only
        # to satisfy ``start_with_eviction_compat`` for tests pending
        # full removal in M5/M6.

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
            invalidate_process_scan_cache()  # issue #392
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
                invalidate_process_scan_cache()  # issue #392
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
                invalidate_process_scan_cache()  # issue #392
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
            ready, _logs = wait_for_server_ready(
                port, timeout=readiness_timeout, process=process
            )
            if not ready:
                # Terminate new process
                stop_server_by_pid(new_pid)
                self.running.pop(port, None)
                invalidate_process_scan_cache()  # issue #392

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
                        invalidate_process_scan_cache()  # issue #392
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
                        invalidate_process_scan_cache()  # issue #392
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
            invalidate_process_scan_cache()  # issue #392

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
                    invalidate_process_scan_cache()  # issue #392
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
                    invalidate_process_scan_cache()  # issue #392
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
        }
