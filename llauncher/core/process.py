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

    # Sanitize config name to prevent path traversal and invalid characters
    sanitized_name = re.sub(r'[^\w\-]', '_', config.name)
    log_file = LOG_DIR / f"{sanitized_name}-{port}.log"
    # Resolve to ensure we stay within LOG_DIR
    log_file = log_file.resolve()

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


def stop_server_by_port(port: int) -> bool:
    """Stop a llama-server running on the given port.

    Args:
        port: Port number of the server to stop.

    Returns:
        True if a server was found and stopped, False otherwise.
    """
    process = find_server_by_port(port)
    if process:
        return stop_server_by_pid(process.pid)
    return False


def stop_server_by_pid(pid: int) -> bool:
    """Stop a llama-server process by PID.

    Args:
        pid: Process ID to stop.

    Returns:
        True if process was stopped, False if not found.
    """
    try:
        process = psutil.Process(pid)

        # Find all llama-server children
        try:
            children = process.children(recursive=True)
            for child in children:
                child.terminate()
            psutil.wait_procs(children, timeout=3)
        except psutil.NoSuchProcess:
            pass

        # Terminate the main process
        process.terminate()
        try:
            process.wait(timeout=5)
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

    # If model name provided, search for matching log files
    if model_name is not None and port is None:
        for log_file in LOG_DIR.glob(f"{model_name}-*.log"):
            return _tail_file(log_file, lines)

    if port:
        # Find matching log file
        for log_file in LOG_DIR.glob(f"*-{port}.log"):
            return _tail_file(log_file, lines)

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

    Returns:
        Tuple of (is_ready, recent_log_lines).
        is_ready is True if server became ready within timeout.
        recent_log_lines contains the last 50 log lines for debugging.
    """
    import socket
    import time

    start_time = time.time()
    last_logs = []

    while time.time() - start_time < timeout:
        # ADR-014: check cancel at the natural poll cadence — no new threads.
        if cancel_check is not None and cancel_check():
            proc = find_server_by_port(port)
            if proc:
                last_logs = stream_logs(pid=proc.pid, lines=50)
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
            # Port is listening, now check logs for "listening" or "ready"
            proc = find_server_by_port(port)
            if proc:
                logs = stream_logs(pid=proc.pid, lines=20)
                last_logs = logs
                log_text = "\n".join(logs).lower()

                # Check for various ready indicators
                ready_indicators = [
                    "listening",
                    "server started",
                    "ready to serve",
                    "rest api listening",
                ]

                if any(indicator in log_text for indicator in ready_indicators):
                    return True, logs

        time.sleep(check_interval)

    # Timeout - return whatever logs we have
    proc = find_server_by_port(port)
    if proc:
        last_logs = stream_logs(pid=proc.pid, lines=50)

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
