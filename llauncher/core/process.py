"""Process management for llama-server instances."""

import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

import psutil

from llauncher.core import log_rotation, settings
from llauncher.core.settings import (
    LLAMA_SERVER_PATH,
    DEFAULT_PORT,
    BLACKLISTED_PORTS,
)
from llauncher.models.config import ModelConfig


DEFAULT_SERVER_BINARY = LLAMA_SERVER_PATH

# Re-export for backward compatibility — historical code imports
# ``LOG_DIR`` from this module, and tests use
# ``patch("llauncher.core.process.LOG_DIR", ...)``. ADR-013 made the
# directory env-configurable; new code should read
# ``settings.LAUNCHER_LOG_DIR`` directly.
#
# IMPORTANT: this alias is captured at *import* time. Patching
# ``os.environ["LAUNCHER_LOG_DIR"]`` after import does NOT update this
# symbol — the supported test seam is ``patch(..."LOG_DIR", ...)`` on
# this module. Don't expect env mutations to propagate here.
LOG_DIR = settings.LAUNCHER_LOG_DIR

# Heuristic for the bounded tail in :func:`_tail_file`: assume each line
# averages ~160 bytes (timestamp + message; llama-server logs trend
# longer but errors-on-the-low-side are cheap and we double the window
# below). Tunable only via direct override; not exposed as an env var.
_AVG_LOG_LINE_BYTES = 160


def find_available_port(
    preferred_port: int | None = None,
    start: int | None = None,
    end: int = 8999
) -> tuple[bool, int, str]:
    """Find an available port for a new server.

    Tries the preferred port first, then scans the range for the first
    available port. Respects BLACKLISTED_PORTS from settings.

    Args:
        preferred_port: Preferred port to try first.
        start: Start of port range to scan (defaults to DEFAULT_PORT from settings).
        end: End of port range to scan.

    Returns:
        Tuple of (success, port, message).
    """
    if start is None:
        start = DEFAULT_PORT

    # Try preferred port first
    if preferred_port is not None:
        if not is_port_in_use(preferred_port) and preferred_port not in BLACKLISTED_PORTS:
            return True, preferred_port, f"Using preferred port {preferred_port}"

    # Scan range for first available
    for port in range(start, end + 1):
        if port in BLACKLISTED_PORTS:
            continue  # Skip blacklisted ports
        if preferred_port is not None and port == preferred_port:
            continue  # Skip preferred (already tried)
        if not is_port_in_use(port):
            return True, port, f"Auto-allocated port {port}"

    return False, 0, "No available ports in range"


def build_command(
    config: ModelConfig,
    port: int,
    host: str = "0.0.0.0",
    server_bin: Path | None = None
) -> list[str]:
    """Build the command line for starting a llama-server.

    Args:
        config: Model configuration.
        port: Port to bind the server to (resolved at runtime).
        host: Host to bind the server to (defaults to 0.0.0.0).
        server_bin: Path to llama-server binary.

    Returns:
        List of command line arguments.
    """
    if server_bin is None:
        server_bin = DEFAULT_SERVER_BINARY
    cmd = [str(server_bin)]

    # Model path
    cmd.extend(["-m", config.model_path])

    # Multimodal projector (optional)
    if config.mmproj_path:
        cmd.extend(["--mmproj", config.mmproj_path])

    # GPU layers
    cmd.extend(["--n-gpu-layers", str(config.n_gpu_layers)])

    # Network (port and host are now runtime parameters)
    cmd.extend(["--host", host, "--port", str(port)])

    # Context size
    cmd.extend(["-c", str(config.ctx_size)])

    # Threads (optional)
    if config.threads:
        cmd.extend(["--threads", str(config.threads)])

    # Threads batch
    cmd.extend(["--threads-batch", str(config.threads_batch)])

    # Ubatch size
    cmd.extend(["--ubatch-size", str(config.ubatch_size)])

    # Batch size (optional)
    if config.batch_size is not None:
        cmd.extend(["--batch-size", str(config.batch_size)])

    # Flash attention
    cmd.extend(["--flash-attn", config.flash_attn])

    # No mmap
    if config.no_mmap:
        cmd.append("--no-mmap")

    # Cache types (optional)
    if config.cache_type_k:
        cmd.extend(["--cache-type-k", config.cache_type_k])

    if config.cache_type_v:
        cmd.extend(["--cache-type-v", config.cache_type_v])

    # CPU MOE threads (optional)
    if config.n_cpu_moe:
        cmd.extend(["--n-cpu-moe", str(config.n_cpu_moe)])

    # Parallel/server slots
    if config.parallel and config.parallel > 1:
        cmd.extend(["--parallel", str(config.parallel)])

    # Sampling parameters
    if config.temperature is not None:
        cmd.extend(["--temp", str(config.temperature)])
    if config.top_k is not None:
        cmd.extend(["--top-k", str(config.top_k)])
    if config.top_p is not None:
        cmd.extend(["--top-p", str(config.top_p)])
    if config.min_p is not None:
        cmd.extend(["--min-p", str(config.min_p)])
    if config.repeat_penalty is not None:
        cmd.extend(["--repeat-penalty", str(config.repeat_penalty)])
    if config.reverse_prompt:
        cmd.extend(["--reverse-prompt", config.reverse_prompt])

    # Memory management
    if config.mlock:
        cmd.append("--mlock")

    # Extra args (parse free-form string into arguments)
    if config.extra_args:
        cmd.extend(shlex.split(config.extra_args))

    return cmd


def log_path_for(config_name: str, port: int) -> Path:
    """Return the canonical per-server log path for ``(config_name, port)``.

    Single source of truth for the log filename so that the readiness check
    (:func:`wait_for_server_ready`) reads exactly the file that
    :func:`start_server` writes — see issue #145, where a swap left two
    ``*-{port}.log`` files and a port-only glob could read the stopped
    occupant's log instead of the new one.

    NOTE: the sanitization is *lossy* — ``re.sub`` collapses every non
    ``[\\w-]`` character to ``_``, so distinct config names that differ
    only in those characters (e.g. ``LFM2-350M-Pro.f16`` vs
    ``LFM2-350M-Pro_f16``) map to the same file. That name-collision hazard
    is tracked as a separate issue; it is safe *here* only because the
    identical transform is applied on both the write side and the read side.
    """
    sanitized_name = re.sub(r"[^\w\-]", "_", config_name)
    return (LOG_DIR / f"{sanitized_name}-{port}.log").resolve()


def _newest_log(paths) -> Path | None:
    """Return the most-recently-modified path from ``paths`` (or ``None``).

    Disambiguates when several log files match a port/name pattern: the
    freshest file belongs to the current occupant. Robust to a file
    vanishing mid-scan — a stat failure sorts the path last rather than
    raising. Replaces the previous "return the first glob hit" behavior,
    whose ordering was arbitrary (issue #145).
    """
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return float("-inf")

    candidates = list(paths)
    if not candidates:
        return None
    return max(candidates, key=_mtime)


def start_server(
    config: ModelConfig,
    port: int,
    host: str = "0.0.0.0",
    server_bin: Path | None = None,
) -> subprocess.Popen:
    """Start a llama-server process.

    Args:
        config: Model configuration.
        port: Port to bind the server to.
        host: Host to bind the server to (defaults to 0.0.0.0).
        server_bin: Path to llama-server binary.

    Returns:
        The subprocess.Popen object for the started server.

    Raises:
        FileNotFoundError: If server binary doesn't exist.
        subprocess.SubprocessError: If process fails to start.
    """
    if server_bin is None:
        server_bin = DEFAULT_SERVER_BINARY
    if not server_bin.exists():
        raise FileNotFoundError(f"Server binary not found: {server_bin}")

    cmd = build_command(config, port, host, server_bin)

    # Create logs directory
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Canonical per-server log path. Shared with wait_for_server_ready via
    # log_path_for() so readiness reads exactly the file we write here, and
    # resolved to stay within LOG_DIR (path-traversal guard). See #145.
    log_file = log_path_for(config.name, port)

    # Rotate before opening, per ADR-013. Prevents an unbounded log from
    # absorbing yet another run on top of however much it already has.
    log_rotation.rotate_if_needed(
        log_file,
        max_bytes=settings.LAUNCHER_LOG_MAX_BYTES,
        keep=settings.LAUNCHER_LOG_KEEP,
    )

    # Append-mode (ADR-013) preserves the previous run's logs across
    # restart — historically these were the most useful debugging
    # artifact, and the old ``"w"`` mode destroyed them on every start.
    # The banner line below makes the boundary between runs grep-friendly.
    banner = f"=== started at {datetime.now().isoformat()} port={port} ===\n"
    try:
        with open(log_file, "a", encoding="utf-8") as log:
            log.write(banner)
            log.flush()  # ensure the banner lands before subprocess inherits the FD
            process = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # Create new process group for clean termination
            )
        # NOTE: the parent's Python ``with`` block closes the wrapper
        # here, but the child has its own duplicated raw file
        # descriptor pointing at the same kernel inode — closing the
        # parent's wrapper does not affect the child's writes. This
        # holds on Linux and macOS regardless of ``start_new_session``.
        return process
    except OSError as e:
        raise OSError(f"Failed to create log file {log_file}: {e}")


def stop_server_by_port(
    port: int,
    *,
    child_grace_s: float | None = None,
    grace_s: float | None = None,
) -> bool:
    """Stop a llama-server running on the given port.

    Args:
        port: Port number of the server to stop.
        child_grace_s: SIGTERM grace for children; see
            :func:`stop_server_by_pid`.
        grace_s: SIGTERM grace for the main process; see
            :func:`stop_server_by_pid`.

    Returns:
        True if a server was found and stopped, False otherwise.
    """
    process = find_server_by_port(port)
    if process:
        return stop_server_by_pid(
            process.pid, child_grace_s=child_grace_s, grace_s=grace_s
        )
    return False


def stop_server_by_pid(
    pid: int,
    *,
    child_grace_s: float | None = None,
    grace_s: float | None = None,
) -> bool:
    """Stop a llama-server process by PID.

    SIGTERM first, then SIGKILL for anything that outlives its grace
    period — children included (issue #140: the previous implementation
    only escalated the main process, so a child that ignored SIGTERM
    leaked past the stop). Grace periods default from settings *at call
    time* (``LAUNCHER_STOP_CHILD_GRACE_S`` / ``LAUNCHER_STOP_GRACE_S``)
    so env-configured profiles and test patches both take effect — the
    settings module is referenced as an attribute, not imported as a
    bound name, for exactly the import-time-capture reason documented
    on ``LOG_DIR`` above.

    Args:
        pid: Process ID to stop.
        child_grace_s: Seconds to wait for children after SIGTERM before
            SIGKILL. ``None`` → ``settings.LAUNCHER_STOP_CHILD_GRACE_S``.
        grace_s: Seconds to wait for the main process after SIGTERM
            before SIGKILL. ``None`` → ``settings.LAUNCHER_STOP_GRACE_S``.

    Returns:
        True if process was stopped, False if not found.
    """
    if child_grace_s is None:
        child_grace_s = settings.LAUNCHER_STOP_CHILD_GRACE_S
    if grace_s is None:
        grace_s = settings.LAUNCHER_STOP_GRACE_S

    try:
        process = psutil.Process(pid)

        # Find all llama-server children
        try:
            children = process.children(recursive=True)
            for child in children:
                child.terminate()
            _, alive = psutil.wait_procs(children, timeout=child_grace_s)
            # Grace expired — escalate so no child outlives the stop
            # (issue #140). A child may exit between the wait and the
            # kill; that's success, not an error.
            for child in alive:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass

        # Terminate the main process
        process.terminate()
        try:
            process.wait(timeout=grace_s)
        except psutil.TimeoutExpired:
            process.kill()

        return True

    except psutil.NoSuchProcess:
        return False


def find_server_by_port(port: int) -> psutil.Process | None:
    """Find a llama-server process listening on the given port.

    Args:
        port: Port number to search for.

    Returns:
        The process if found, None otherwise.
    """
    for proc in psutil.process_iter(["pid", "cmdline", "name"]):
        try:
            cmdline = proc.cmdline()
            if not cmdline:
                continue

            # Check if this is a llama-server with the right port
            if "llama-server" in proc.name() or any("llama-server" in c for c in cmdline):
                # Check command line for port
                for i, arg in enumerate(cmdline):
                    if arg in ("--port", "-p") and i + 1 < len(cmdline):
                        if cmdline[i + 1] == str(port):
                            return proc
                    # Also check port in the command
                    if f"--port={port}" in arg or f"-p{port}" in arg:
                        return proc

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return None


def find_all_llama_servers() -> list[psutil.Process]:
    """Find all running llama-server processes.

    Returns:
        List of all llama-server processes.
    """
    servers = []

    for proc in psutil.process_iter(["pid", "cmdline", "name"]):
        try:
            cmdline = proc.cmdline()
            if not cmdline:
                continue

            if "llama-server" in proc.name() or any("llama-server" in c for c in cmdline):
                servers.append(proc)

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return servers


# Sentinel returned by :func:`find_all_llama_servers_annotated` when a
# process is iterable but its cmdline cannot be read (AccessDenied) — the
# pid is still observable but the port cannot be extracted. Callers that
# care about port-keyed reconciliation should skip pids in this state and
# log a warning once per pid.
class _UnreadableCmdline:
    pass


def find_all_llama_servers_annotated() -> list[tuple[psutil.Process, int | None, bool]]:
    """Find all running llama-server processes with port annotation.

    Companion to :func:`find_all_llama_servers` for ADR-015 orphan
    discovery. Returns each process paired with the port extracted from
    its argv (when readable), and a ``cmdline_unreadable`` flag that
    distinguishes "process exists but we couldn't read its cmdline" from
    "process exists and has no port in its cmdline."

    Mirrors the port-extraction idiom in
    :meth:`llauncher.state.LauncherState.refresh_running_servers` so the
    two scans agree on what counts as a port.

    Returns:
        List of ``(proc, port, cmdline_unreadable)`` tuples. ``port`` is
        ``None`` when no ``--port`` argument was found OR when cmdline
        was unreadable; the third element disambiguates these.
    """
    annotated: list[tuple[psutil.Process, int | None, bool]] = []

    for proc in psutil.process_iter(["pid", "cmdline", "name"]):
        try:
            try:
                cmdline = proc.cmdline()
            except psutil.AccessDenied:
                # We can see the pid (name() succeeded for the matcher
                # below to even be relevant) but cannot read argv. Surface
                # the pid with the unreadable flag so callers can dedupe
                # warnings rather than re-checking each scan tick.
                if "llama-server" in (proc.name() or ""):
                    annotated.append((proc, None, True))
                continue

            if not cmdline:
                continue

            if "llama-server" not in proc.name() and not any(
                "llama-server" in c for c in cmdline
            ):
                continue

            port: int | None = None
            for i, arg in enumerate(cmdline):
                if arg == "--port" and i + 1 < len(cmdline):
                    try:
                        port = int(cmdline[i + 1])
                    except (TypeError, ValueError):
                        port = None
                    break

            annotated.append((proc, port, False))

        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue

    return annotated


def stream_logs(pid: int | None = None, model_name: str | None = None, lines: int = 100) -> list[str]:
    """Stream recent log lines for a process.

    Args:
        pid: Process ID (optional).
        model_name: Model name to search logs by (optional).
        lines: Number of lines to return.

    Returns:
        List of log lines.
    """
    # If PID provided, try to get port from running process
    port = None
    if pid is not None:
        try:
            process = psutil.Process(pid)
            cmdline = process.cmdline()

            # Extract port from command line
            for i, arg in enumerate(cmdline or []):
                if arg == "--port" and i + 1 < len(cmdline):
                    port = cmdline[i + 1]
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # If model name provided, search for matching log files. Sanitize the
    # name with the SAME transform start_server uses (log_path_for): the
    # files on disk are stored sanitized, and an un-sanitized glob would
    # both miss the real file (``LFM2-350M-Pro.f16`` vs the stored
    # ``LFM2-350M-Pro_f16``) and risk interpreting name characters like
    # ``[`` as glob metacharacters. When several match (a name reused
    # across runs), prefer the most recently written — glob order is
    # otherwise arbitrary (issue #145).
    if model_name is not None and port is None:
        safe_name = re.sub(r"[^\w\-]", "_", model_name)
        match = _newest_log(LOG_DIR.glob(f"{safe_name}-*.log"))
        if match is not None:
            return _tail_file(match, lines)

    if port:
        # Multiple models can leave a ``*-{port}.log`` behind across a swap;
        # the freshest belongs to the current occupant (issue #145).
        match = _newest_log(LOG_DIR.glob(f"*-{port}.log"))
        if match is not None:
            return _tail_file(match, lines)

    return []


def _tail_file(path: Path, lines: int) -> list[str]:
    """Read the last ``lines`` lines from ``path``.

    Bounded-tail implementation per ADR-013: reads at most a window of
    ``lines * _AVG_LOG_LINE_BYTES * 2`` bytes from the end of the file
    rather than slurping the whole file. With the default 100 lines and
    160 bytes/line that's a 32 KiB window, regardless of file size.

    The window is doubled past the heuristic so we still satisfy
    ``lines`` even when individual lines are unusually long — and if
    they're so long that even the doubled window underflows, we silently
    return what we found rather than escalating to a full read.

    **Caller contract (ADR-013 §Consequences):** ``len(result)`` may be
    *less* than ``lines``. Stack traces from ``llama-server`` routinely
    exceed 500 bytes per line, in which case a 100-line request only
    yields ~64 entries. The return is always a complete-from-the-tail
    slice (no partial first line), but may be short.
    """
    if not path.exists() or lines <= 0:
        return []

    try:
        size = path.stat().st_size
        if size == 0:
            return []
        window = min(size, lines * _AVG_LOG_LINE_BYTES * 2)
        with open(path, "rb") as f:
            f.seek(size - window)
            data = f.read(window)
        text = data.decode("utf-8", errors="replace")
        all_lines = text.splitlines()
        # If the window started mid-file, the first line we read is
        # almost certainly cut at an arbitrary byte. Drop it.
        if window < size and all_lines:
            all_lines = all_lines[1:]
        return all_lines[-lines:]
    except OSError:
        return []


def wait_for_server_ready(
    port: int,
    timeout: int = 120,
    check_interval: float = 1.0,
    cancel_check=None,
    model_name: str | None = None,
) -> tuple[bool, list[str]]:
    """Wait for a llama-server to become ready to accept requests.

    Polls for the server to be listening on the port and checks logs
    for confirmation that the model has loaded.

    Args:
        port: Port number the server should be listening on.
        timeout: Maximum seconds to wait (default: 120).
        check_interval: Seconds between checks (default: 1.0).
        cancel_check: Optional ``Callable[[], bool]`` invoked once per
            poll tick. If it returns True the poll aborts and returns
            ``(False, last_logs)`` immediately. Per ADR-014 — used by
            ``operations.swap``/``start`` to react to a cancel request
            without spinning a separate thread.
        model_name: Config name of the server being launched. When given,
            readiness reads that model's **exact** log file
            (:func:`log_path_for`) instead of resolving by port. This is
            the fix for issue #145: across a swap two ``*-{port}.log``
            files coexist (stopped + new occupant), and a port-only lookup
            could read the stopped model's log — whose tail lacks the
            "listening" line — making a perfectly healthy new server look
            like it never came up and timing the swap out. Callers that
            don't name a model fall back to the legacy port-based lookup.

    Returns:
        Tuple of (is_ready, recent_log_lines).
        is_ready is True if server became ready within timeout.
        recent_log_lines contains the last 50 log lines for debugging.
    """
    import socket
    import time

    def _attempt_logs(n: int) -> list[str]:
        # Prefer the exact log for the model we're waiting on so a stale
        # ``*-{port}.log`` from a prior occupant cannot shadow it (#145).
        if model_name is not None:
            return _tail_file(log_path_for(model_name, port), n)
        found = find_server_by_port(port)
        return stream_logs(pid=found.pid, lines=n) if found else []

    # Check for various ready indicators (hoisted: constant per call).
    ready_indicators = (
        "listening",
        "server started",
        "ready to serve",
        "rest api listening",
    )

    start_time = time.time()
    last_logs: list[str] = []

    while time.time() - start_time < timeout:
        # ADR-014: check cancel at the natural poll cadence — no new threads.
        if cancel_check is not None and cancel_check():
            last_logs = _attempt_logs(50) or last_logs
            return False, last_logs

        # Check if port is listening
        port_ready = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            port_ready = (result == 0)
        except OSError:
            pass

        if port_ready:
            # Port is listening, now check the server's log for a ready line.
            logs = _attempt_logs(20)
            if logs:
                last_logs = logs
            log_text = "\n".join(logs).lower()
            if any(indicator in log_text for indicator in ready_indicators):
                return True, logs

        time.sleep(check_interval)

    # Timeout - return whatever logs we have
    last_logs = _attempt_logs(50) or last_logs
    return False, last_logs


def is_port_in_use(port: int) -> bool:
    """Check if a port is currently in use by any process.

    Args:
        port: Port number to check.

    Returns:
        True if port is in use, False otherwise.
    """
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = proc.cmdline()
            if not cmdline:
                continue

            for i, arg in enumerate(cmdline):
                if arg in ("--port", "-p") and i + 1 < len(cmdline):
                    if cmdline[i + 1] == str(port):
                        return True
                if arg.startswith(f"--port={port}") or arg.startswith(f"-p{port}"):
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return False
