"""Process management for llama-server instances."""

import errno
import hashlib
import logging
import re
import shlex
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psutil

from llauncher.core import log_rotation, settings
from llauncher.core.settings import (
    LLAMA_SERVER_PATH,
    DEFAULT_PORT,
    BLACKLISTED_PORTS,
)
from llauncher.models.config import ModelConfig
from llauncher.util.cache import _TTLCache

logger = logging.getLogger(__name__)

# EADDRINUSE and its Winsock twin (10048), used by the bind-probe
# fallback in :func:`_bind_probe_port_in_use`.
_EADDRINUSE_ERRNOS = frozenset(
    {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", 10048)}
)


DEFAULT_SERVER_BINARY = LLAMA_SERVER_PATH

# Issue #392: LauncherState.refresh() triggers two full psutil.process_iter
# scans (find_all_llama_servers + discover_all), and
# refresh() itself is called redundantly up to 3-4x per Streamlit rerun
# (once per tab render — Streamlit's `st.tabs()` executes *every* tab's
# body on every script run; only the active tab's output is switched
# client-side). A full process-table scan on Windows is expensive enough
# (measured ~8.4-8.6s for /status vs 8ms for /node-info; docstrings below
# cite an elastic 3.1-12.3s range, #309) that this redundancy alone
# produced the observed UI stall. A short TTL cache in front of each scan
# is meant to collapse the redundant calls within one script run while
# staying invisible across genuine start/stop actions, which explicitly
# invalidate the cache regardless of TTL (see invalidate_process_scan_cache
# and llauncher.state's self.running mutation sites).
#
# Issue #494 (and #418): the TTL here stays SHORT — 3s — on purpose.
# On the delegated topology (ADR-018 production deployment,
# delegation.should_delegate() true) the UI process never calls
# start_server/stop_server_by_pid itself; it POSTs to the agent, which
# invalidates its *own* interpreter's cache instance. The Streamlit
# process's cache is never invalidated on that path, so this TTL is the
# ONLY staleness bound on the delegated topology: a post-mutation
# state.refresh() can serve a pre-mutation scan for up to the TTL (#418).
# Lengthening it lengthens that stale window proportionally, so it stays
# at 3s until #418 lands a real cross-process invalidation.
#
# What this TTL does NOT solve: the same-run double refresh() —
# dashboard.py:54 and model_registry.py:48 both fire under st.tabs on one
# script run, and a single scan (~6-12s, #309) outlasts any TTL short
# enough to be safe here, so the second caller misses regardless. That is
# a call-graph problem, not a cache-tuning one; the fix is to hoist the
# refresh so one run does one refresh.
# tracked: one refresh per run, hoisted to app.py — #497
#
# In-process start/stop are unaffected either way: they call
# invalidate_process_scan_cache() intrinsically (#402/#414), which clears
# the cache immediately regardless of TTL.
#
# Each scan function gets its OWN cache key — they return different shapes
# (bare Process list vs list[ServerProcessInfo]) and must never share
# a cached result.
_PROCESS_SCAN_CACHE = _TTLCache(ttl_seconds=3)
_SCAN_KEY_ALL_SERVERS = "find_all_llama_servers"
_SCAN_KEY_ALL_SERVERS_ANNOTATED = "find_all_llama_servers_annotated"

# Issue #521: on Windows, psutil.Process.cmdline() is a per-process handle
# open + PEB read with no batch path — walking ~315 processes and reading
# cmdline() on every one of them measured a 3.1s floor, dominated by
# protected/system processes. The fix is name-first filtering: fetch only
# ["pid", "name"] for the walk (median 1.1ms), and pay for cmdline() only
# on processes whose name is worth reading argv from — either the
# configured llama-server binary (settings.LLAMA_SERVER_PATH) or a known
# interpreter/shell name (the wrapper-invocation case, e.g. "python
# launch.py ... llama-server ..." or "bash run.sh ... llama-server ...",
# where "llama-server" only appears in argv, not in name()).
# Everything else never gets a cmdline() call — measured 1.5ms for the
# same walk with this filter applied.
_DEFAULT_BINARY_NAMES = ("llama-server", "llama-server.exe")

# Interpreters/shells/launchers that can front a llama-server invocation.
# Exact (case-insensitive, ``.exe`` stripped) matches only: a prefix rule
# would drag in unrelated processes ("shellexperiencehost", "pycharm")
# and hand each of them the per-process cmdline() cost this filter
# exists to avoid. Versioned CPython names are covered by the regex
# below rather than by listing every "python3.NN".
_INTERPRETER_NAMES = frozenset({
    "py", "pyw",
    "sh", "bash", "zsh", "dash", "ash", "fish", "ksh", "csh", "tcsh",
    "cmd", "powershell", "pwsh",
    "perl", "ruby", "node",
    "env", "nohup", "wsl", "sudo",
})

# "python", "pythonw", "python3", "python3.12", "pythonw3.12", …
_PYTHON_NAME_RE = re.compile(r"^pythonw?\d*(\.\d+)?$")

# Cache of (configured LLAMA_SERVER_PATH, derived name set). Rebuilt when
# the setting changes so tests can monkeypatch ``settings`` freely; a
# plain module constant would freeze the import-time value.
_BINARY_NAMES_CACHE: tuple[object, frozenset[str]] | None = None


def _configured_binary_names() -> frozenset[str]:
    """Lowercased llama-server binary names worth a ``cmdline()`` read.

    The configured binary's filename (``settings.LLAMA_SERVER_PATH``)
    plus the two defaults, so a seat that runs a renamed/forked build
    ("llama-server-b4567.exe") is still discovered.
    """
    global _BINARY_NAMES_CACHE
    configured = getattr(settings, "LLAMA_SERVER_PATH", None)
    cached = _BINARY_NAMES_CACHE
    if cached is not None and cached[0] == configured:
        return cached[1]
    names = {name.lower() for name in _DEFAULT_BINARY_NAMES}
    if configured:
        configured_name = Path(configured).name.lower()
        if configured_name:
            names.add(configured_name)
    frozen = frozenset(names)
    _BINARY_NAMES_CACHE = (configured, frozen)
    return frozen


def _is_cmdline_scan_candidate(name: str | None) -> bool:
    """Name-first filter (#521): is ``name`` worth a ``cmdline()`` read?

    True when ``name`` is a configured llama-server binary name (direct
    invocation) or a known interpreter/shell name (the wrapper-invocation
    case — the actual "llama-server" evidence lives in argv, which this
    function's caller has not read yet). Comparison is case-insensitive;
    a trailing ``.exe`` is stripped before the interpreter-name check so
    "python3.exe"/"cmd.exe" match the same as their POSIX names.
    """
    name_lower = (name or "").lower()
    if not name_lower:
        return False
    if any(binary in name_lower for binary in _configured_binary_names()):
        return True
    stem = name_lower[:-4] if name_lower.endswith(".exe") else name_lower
    return stem in _INTERPRETER_NAMES or bool(_PYTHON_NAME_RE.match(stem))


class ExtraArgsError(ValueError):
    """``extra_args`` could not be turned into argv at launch time.

    The one exception type callers catch for every ``extra_args`` defect
    (ADR-LLNCH-026 / issue #477). ``extra_args`` carries llama-server flags
    verbatim with no pydantic content validation, so *both* of its failure
    modes — unparseable shell quoting and a llauncher-owned flag — first
    become observable here, in :func:`build_command`, the single
    enforcement point. ``operations.start`` / ``operations.swap`` catch
    this base class alongside their other launch-failure exceptions, so
    neither subclass can escape as a bare ``ValueError``.
    """


class DeniedExtraArgError(ExtraArgsError):
    """``extra_args`` carries a flag llauncher owns and enforces at launch.

    Raised by :func:`build_command` — the single enforcement point for
    :data:`DENIED_EXTRA_ARG_FLAGS` (ADR-LLNCH-026 / issue #477). Callers
    (``start_server``, ``operations.start``/``operations.swap``) catch this
    alongside their other launch-failure exceptions and surface it as a
    clear, typed error rather than letting a malformed/hostile argv reach
    ``subprocess.Popen``.
    """


class MalformedExtraArgsError(ExtraArgsError):
    """``extra_args`` is not valid shell-token text (unbalanced quoting).

    ADR-LLNCH-026 removed all pydantic content validation from ``extra_args``,
    so the UI/MCP/CLI write path accepts any string the operator types —
    deliberately, since llama-server's own parser is the authority on its
    flags. The consequence is that ``shlex.split`` can fail here, at
    launch. Raising a typed error (rather than letting ``shlex``'s bare
    ``ValueError`` out) keeps that failure inside the launch-error contract
    ``operations.start``/``operations.swap`` already handle, instead of
    escaping as an unhandled exception.
    """


# Deny-list of llama-server flags llauncher manages at its own boundary
# (security-hardening-plan §3 C7, Issue #81; ADR-LLNCH-019). These flags
# must not appear in ``ModelConfig.extra_args`` because:
#
# * ``--api-key`` / ``--alias`` — security-sensitive identity that
#   llauncher owns. ``--alias`` is emitted below from
#   ``ModelConfig.name`` per issue #120 / EMIT-CANONICAL; a config
#   slipping one of these in would silently override llauncher's minted
#   identity.
# * ``-m`` / ``--model`` — set below from ``ModelConfig.model_path``.
#   Duplication bypasses the path validator on ``model_path``.
# * ``--host`` / ``--port`` — supplied at start time as runtime
#   parameters (ADR-010). An override here defeats port allocation and
#   the loopback-default binding (C2, PR #75).
# * ``--metrics`` / ``--slots`` / ``--no-slots`` — llauncher's own
#   observability and exposure contract (ADR-LLNCH-019). llauncher
#   always emits ``--slots`` or ``--no-slots`` explicitly, in both
#   directions, so effective policy is config-driven, never the binary
#   default; an ``extra_args`` duplicate could silently defeat that.
#
# ADR-LLNCH-026 / issue #477: this is now the *only* enforcement point for the
# deny-list — a pydantic field validator used to duplicate this check at
# config-construction/load time; that duplication is gone. A malformed
# config on disk is caught here, at launch, not earlier.
DENIED_EXTRA_ARG_FLAGS: frozenset[str] = frozenset({
    "--api-key",
    "--alias",
    "-m",
    "--model",
    "--host",
    "--port",
    "--metrics",
    "--slots",
    "--no-slots",
})


def _check_extra_args_deny_list(name: str, tokens: list[str]) -> None:
    """Raise :class:`DeniedExtraArgError` if ``tokens`` carries a denied flag.

    Matches both the bare (``--api-key foo``) and equals (``--api-key=foo``)
    forms — the head before ``=`` is what is compared.

    ``name`` is the offending :attr:`ModelConfig.name` — issue #462: a
    config-hygiene message that names the flag but not the config entry
    is unactionable once more than one model is on the wire (the sibling
    :class:`MalformedExtraArgsError`, raised in :func:`build_command` below,
    already names it; this one didn't).
    """
    for token in tokens:
        head = token.split("=", 1)[0]
        if head in DENIED_EXTRA_ARG_FLAGS:
            raise DeniedExtraArgError(
                f"extra_args for model {name!r} contains llauncher-managed "
                f"flag {head!r} — set it via the dedicated ModelConfig "
                f"field (or runtime parameter), or remove it. llauncher "
                f"enforces this at launch time (ADR-LLNCH-026 / issue #477)."
            )


def invalidate_process_scan_cache() -> None:
    """Purge the cached process-table scans (issue #392).

    Call this immediately after any mutation that changes what's running
    (start/stop/rollback) so the next refresh() reflects reality instead of
    serving a stale scan for up to the cache's TTL.
    """
    _PROCESS_SCAN_CACHE.invalidate_all()

# Re-export for backward compatibility — historical code imports
# ``LOG_DIR`` from this module, and tests use
# ``patch("llauncher.core.process.LOG_DIR", ...)``. ADR-LLNCH-013 made the
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

    # Issue #521: read the socket table ONCE for the whole scan rather
    # than calling is_port_in_use() per candidate — a range scan over
    # 8081..8999 would otherwise be ~900 net_connections() calls. Public
    # behavior is unchanged; only the number of syscalls is.
    listening = _listening_ports()

    def _in_use(candidate: int) -> bool:
        if listening is None:
            # Socket table unreadable (AccessDenied); fall back to the
            # per-port bind probe, same as is_port_in_use would.
            return _bind_probe_port_in_use(candidate)
        return candidate in listening

    # Try preferred port first
    if preferred_port is not None:
        if not _in_use(preferred_port) and preferred_port not in BLACKLISTED_PORTS:
            return True, preferred_port, f"Using preferred port {preferred_port}"

    # Scan range for first available
    for port in range(start, end + 1):
        if port in BLACKLISTED_PORTS:
            continue  # Skip blacklisted ports
        if preferred_port is not None and port == preferred_port:
            continue  # Skip preferred (already tried)
        if not _in_use(port):
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

    # Canonical served-model identity (issue #120, EMIT-CANONICAL):
    # ``GET /v1/models`` must report the name llauncher minted for this
    # config — byte-for-byte, no transformation, no sanitization — so
    # ecosystem routers (local-inference-pool) can match the server
    # against llauncher's registry. Without ``--alias`` llama-server
    # reports the GGUF filename/metadata instead. This flag is
    # launcher-owned: ``DENIED_EXTRA_ARG_FLAGS`` keeps it out of
    # ``extra_args`` so a config cannot override the minted identity.
    cmd.extend(["--alias", config.name])

    # Multimodal projector (optional)
    if config.mmproj_path:
        cmd.extend(["--mmproj", config.mmproj_path])

    # GPU layers
    cmd.extend(["--n-gpu-layers", str(config.n_gpu_layers)])

    # Network (port and host are now runtime parameters)
    cmd.extend(["--host", host, "--port", str(port)])

    # Context size
    cmd.extend(["-c", str(config.ctx_size)])

    # Parallel/server slots
    if config.parallel and config.parallel > 1:
        cmd.extend(["--parallel", str(config.parallel)])

    # Prometheus /metrics endpoint (issue #169). Default-on: cheap scrape
    # surface, and the structured source for tps/kv-cache/draft-acceptance
    # telemetry that /slots doesn't cover.
    if config.metrics:
        cmd.append("--metrics")

    # Slots-monitoring endpoint (issue #179 SP-1, ADR-LLNCH-019). The
    # ``llama-server`` binary defaults ``--slots`` to ENABLED (PM-2
    # de-risk finding) — the opposite of a safe default, since /slots
    # includes per-slot prompt text. Emit the flag explicitly in both
    # directions so the effective policy is a pure function of
    # ``config.slots``, never the binary's own default.
    cmd.append("--slots" if config.slots else "--no-slots")

    # Extra args (parse free-form string into arguments). ADR-LLNCH-026 / issue
    # #477: extra_args is a verbatim passthrough with no pydantic content
    # validation — the llauncher-owned deny-list is enforced right here,
    # at launch time, the single enforcement point. A denied flag raises
    # before any argv reaches subprocess.Popen.
    if config.extra_args:
        try:
            extra_tokens = shlex.split(config.extra_args)
        except ValueError as e:
            raise MalformedExtraArgsError(
                f"extra_args for model {config.name!r} is not valid "
                f"shell-token text ({e}) — check the quoting. llauncher "
                f"stores extra_args verbatim and parses it only here, at "
                f"launch (ADR-LLNCH-026 / issue #477)."
            ) from e
        _check_extra_args_deny_list(config.name, extra_tokens)
        cmd.extend(extra_tokens)

    return cmd


# Hex chars of SHA-256 kept in the log filename stem. 8 chars = 32 bits:
# more than enough to separate the handful of model configs a single
# launcher manages, while staying short enough to keep filenames readable.
_NAME_HASH_LEN = 8


def log_stem_for(config_name: str) -> str:
    """Return the filename stem for ``config_name``: ``{sanitized}-{hash}``.

    The **single mint** for the model-name → log-filename mapping (issues
    #63 / #146). Every path that writes, reads, or globs a per-server log
    file derives the name through here — :func:`log_path_for` (writer +
    readiness reader) and :func:`stream_logs` (name-keyed glob). Do not
    re-implement this transform elsewhere.

    The mapping is **injective in practice**: the sanitized prefix keeps
    the filename human-readable/greppable, and the suffix — the first
    ``_NAME_HASH_LEN`` hex chars of the SHA-256 of the *exact* canonical
    name — separates names the lossy sanitizer would otherwise collapse
    (``model.a`` vs ``model_a`` both sanitize to ``model_a`` but hash
    apart). The canonical name itself (``ModelConfig.name``) is never
    constrained or altered; only this envelope-side mapping disambiguates.

    The result contains only ``[A-Za-z0-9_-]`` characters, so it is safe
    to embed literally in a glob pattern (no metacharacters survive the
    sanitizer, and the hex digest has none).
    """
    sanitized = re.sub(r"[^\w\-]", "_", config_name)
    digest = hashlib.sha256(config_name.encode("utf-8")).hexdigest()
    return f"{sanitized}-{digest[:_NAME_HASH_LEN]}"


def log_path_for(config_name: str, port: int) -> Path:
    """Return the canonical per-server log path for ``(config_name, port)``.

    Single source of truth for the log *path* so that the readiness check
    (:func:`wait_for_server_ready`) reads exactly the file that
    :func:`start_server` writes — see issue #145, where a swap left two
    ``*-{port}.log`` files and a port-only glob could read the stopped
    occupant's log instead of the new one.

    The filename stem comes from :func:`log_stem_for`, which is injective
    over config names — distinct models never share a log file even when
    their sanitized names collide (issues #63 / #146).
    """
    return (LOG_DIR / f"{log_stem_for(config_name)}-{port}.log").resolve()


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

    # Rotate before opening, per ADR-LLNCH-013. Prevents an unbounded log from
    # absorbing yet another run on top of however much it already has.
    log_rotation.rotate_if_needed(
        log_file,
        max_bytes=settings.LAUNCHER_LOG_MAX_BYTES,
        keep=settings.LAUNCHER_LOG_KEEP,
    )

    # Append-mode (ADR-LLNCH-013) preserves the previous run's logs across
    # restart — historically these were the most useful debugging
    # artifact, and the old ``"w"`` mode destroyed them on every start.
    # The banner line below makes the boundary between runs grep-friendly.
    #
    # The timestamp is absolute UTC (issue #405): llama-server's own log
    # lines carry only time-since-start offsets, so this header is the
    # wall-clock anchor that lets every relative offset join to the audit
    # ledger's UTC times. The model name is the canonical mint
    # (``ModelConfig.name``, same identity as the ``--alias`` emission) and
    # goes last so a name containing spaces stays contiguous up to the
    # trailing ``===``.
    started_utc = datetime.now(timezone.utc).isoformat()
    banner = f"=== started at {started_utc} port={port} model={config.name} ===\n"
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
        #
        # Issue #402: invalidation lives HERE, intrinsic to the spawn
        # primitive, rather than pushed out to every caller. #392 gave
        # state.py its own invalidate_process_scan_cache() calls, but
        # operations/start.py and operations/swap.py — the actual live
        # orchestration paths — never called it, so a scan taken right
        # after a start could still serve a pre-spawn cached result.
        # Invalidating on the primitive closes the class: no future
        # caller of start_server() can forget.
        invalidate_process_scan_cache()
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
    time* (``LLAUNCHER_STOP_CHILD_GRACE_S`` / ``LLAUNCHER_STOP_GRACE_S``)
    so env-configured profiles and test patches both take effect — the
    settings module is referenced as an attribute, not imported as a
    bound name, for exactly the import-time-capture reason documented
    on ``LOG_DIR`` above.

    Args:
        pid: Process ID to stop.
        child_grace_s: Seconds to wait for children after SIGTERM before
            SIGKILL. ``None`` → ``settings.LLAUNCHER_STOP_CHILD_GRACE_S``.
        grace_s: Seconds to wait for the main process after SIGTERM
            before SIGKILL. ``None`` → ``settings.LLAUNCHER_STOP_GRACE_S``.

    Returns:
        True if process was stopped, False if not found.
    """
    if child_grace_s is None:
        child_grace_s = settings.LLAUNCHER_STOP_CHILD_GRACE_S
    if grace_s is None:
        grace_s = settings.LLAUNCHER_STOP_GRACE_S

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

        # Issue #402: invalidate here, intrinsic to the terminate
        # primitive — see the matching note on start_server(). This is
        # the sole exit that actually terminated a live process, so it's
        # the sole exit that needs to purge the scan cache.
        invalidate_process_scan_cache()
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
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info.get("name")
            if not _is_cmdline_scan_candidate(name):
                continue

            cmdline = proc.cmdline()
            if not cmdline:
                continue

            # Check if this is a llama-server with the right port
            if "llama-server" in name or any("llama-server" in c for c in cmdline):
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

    Cached for ``_PROCESS_SCAN_CACHE``'s TTL (issue #392) under its own key
    — see :func:`invalidate_process_scan_cache`.

    Returns:
        List of all llama-server processes.
    """
    cached = _PROCESS_SCAN_CACHE.get(_SCAN_KEY_ALL_SERVERS)
    if cached is not None:
        return cached

    servers = []

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info.get("name")
            if not _is_cmdline_scan_candidate(name):
                continue

            cmdline = proc.cmdline()
            if not cmdline:
                continue

            if "llama-server" in name or any("llama-server" in c for c in cmdline):
                servers.append(proc)

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    _PROCESS_SCAN_CACHE.set(_SCAN_KEY_ALL_SERVERS, servers)
    return servers


@dataclass(frozen=True)
class ServerProcessInfo:
    """Attribution of a live process identified as (or believed to be) a
    llama-server — issue #466.

    Returned by :func:`verify_pid` (pid-addressed lookup, ~11 ms on
    Windows/measured) and :func:`discover_all` (full process-table walk,
    the elastic 3.1-12.3s term measured in #309 — orphan discovery's only
    legitimate use).

    Attributes:
        pid: OS process id.
        port: Port extracted from ``--port`` in argv, or ``None`` when
            absent or cmdline was unreadable.
        alias: Value of ``--alias`` in argv — the ONE-MINT canonical name
            (#423) — or ``None`` when absent or cmdline was unreadable.
        model_path: Value of ``-m``/``--model`` in argv, or ``None``.
        create_time: ``psutil.Process.create_time()`` — the real process
            start time, replacing ``state.py``'s ``datetime.now()`` lie —
            or ``None`` when unavailable.
        cmdline_unreadable: True when the pid is alive but this uid could
            not read its argv (``psutil.AccessDenied``, #208). When True,
            every other field except ``pid`` is ``None`` — the process is
            "unknown-alive," never dropped as absent.
    """

    pid: int
    port: int | None
    alias: str | None
    model_path: str | None
    create_time: float | None
    cmdline_unreadable: bool = False


def _is_llama_server(name: str, cmdline: list[str]) -> bool:
    """Shared identity predicate for :func:`verify_pid` and :func:`discover_all`.

    A process counts as a llama-server when its ``name()`` contains
    "llama-server" OR any argv element does (covers a direct binary
    invocation as well as a shell/wrapper invocation). Single definition
    per #466 §3 — both scan primitives must agree on what a llama-server
    is.
    """
    return "llama-server" in (name or "") or any("llama-server" in c for c in cmdline)


def _extract_port_from_cmdline(cmdline: list[str]) -> int | None:
    """Return the ``--port`` value from argv, or ``None`` if absent/non-numeric."""
    for i, arg in enumerate(cmdline):
        if arg == "--port" and i + 1 < len(cmdline):
            try:
                return int(cmdline[i + 1])
            except (TypeError, ValueError):
                return None
    return None


def _extract_flag_value(cmdline: list[str], flag: str) -> str | None:
    """Return the value following ``flag`` in argv, or ``None`` if absent."""
    for i, arg in enumerate(cmdline):
        if arg == flag and i + 1 < len(cmdline):
            return cmdline[i + 1]
    return None


def verify_pid(pid: int, *, expect_port: int | None = None) -> ServerProcessInfo | None:
    """Verify a lockfile-claimed pid against the live process table (#466).

    The pid-addressed replacement for ADR-LLNCH-008's reconciliation table
    (``lockfile x pid-alive x argv-match``) and the natural body of
    :func:`llauncher.core.lockfile.reconcile_lockfile`'s ``sentinel_check``
    hook — one handle, one cmdline read (~11 ms measured on Windows),
    never a process-table walk.

    Args:
        pid: The pid a lockfile claims is running a managed server.
        expect_port: When given, the claim's port. A live llama-server
            whose argv ``--port`` disagrees is treated as a corrupted
            claim, not a match (ADR-LLNCH-008: "present, argv mismatch ->
            refuse to act on this port").

    Returns:
        ``None`` when the pid is dead (``NoSuchProcess``/zombie), or is
        alive but not a llama-server (the PID-reuse defense), or is a
        live llama-server whose argv port disagrees with ``expect_port``.
        A :class:`ServerProcessInfo` with ``cmdline_unreadable=True`` when
        the pid is alive but this uid cannot read it (#208 unknown-alive
        — must never be dropped from the roster the way ``None`` is).
        A fully-populated :class:`ServerProcessInfo` for the good case.
    """
    try:
        proc = psutil.Process(pid)
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return None
    except psutil.NoSuchProcess:
        # psutil.ZombieProcess subclasses NoSuchProcess, so this also
        # covers a zombie encountered mid-call rather than via status().
        return None
    except psutil.AccessDenied:
        # Present but this uid cannot even read status — unknown-alive,
        # same posture as lockfile.is_pid_alive (#208).
        return ServerProcessInfo(
            pid=pid, port=None, alias=None, model_path=None,
            create_time=None, cmdline_unreadable=True,
        )

    try:
        cmdline = proc.cmdline()
        name = proc.name()
    except psutil.AccessDenied:
        return ServerProcessInfo(
            pid=pid, port=None, alias=None, model_path=None,
            create_time=None, cmdline_unreadable=True,
        )
    except psutil.NoSuchProcess:
        return None

    if not _is_llama_server(name, cmdline):
        # Alive, readable, but not a llama-server — the pid was reused
        # for something else. Refuse to claim it.
        return None

    port = _extract_port_from_cmdline(cmdline)
    if expect_port is not None and port != expect_port:
        logger.warning(
            "verify_pid: pid %s argv port %s does not match expected port "
            "%s — refusing to treat this as the claimed server (ADR-LLNCH-008)",
            pid, port, expect_port,
        )
        return None

    try:
        create_time = proc.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        create_time = None

    return ServerProcessInfo(
        pid=pid,
        port=port,
        alias=_extract_flag_value(cmdline, "--alias"),
        model_path=(
            _extract_flag_value(cmdline, "-m")
            or _extract_flag_value(cmdline, "--model")
        ),
        create_time=create_time,
        cmdline_unreadable=False,
    )


def discover_all() -> list[ServerProcessInfo]:
    """Find all running llama-server processes via a full process-table walk.

    The world-walk (#466): the elastic 3.1-12.3s term measured in #309,
    kept for its one legitimate purpose — orphan discovery, the sole
    question with no pid to start from. Everything that already has a
    pid to check (a lockfile claim) should call :func:`verify_pid`
    instead.

    Cached for ``_PROCESS_SCAN_CACHE``'s TTL (issue #392) under its own
    key, independent of :func:`find_all_llama_servers`'s cache entry —
    see :func:`invalidate_process_scan_cache`.

    Returns:
        List of :class:`ServerProcessInfo`. ``port``/``alias``/
        ``model_path``/``create_time`` are ``None`` when cmdline was
        unreadable (``cmdline_unreadable=True``) or the corresponding
        argv flag was absent.
    """
    cached = _PROCESS_SCAN_CACHE.get(_SCAN_KEY_ALL_SERVERS_ANNOTATED)
    if cached is not None:
        return cached

    discovered: list[ServerProcessInfo] = []

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = proc.info.get("name")
            if not _is_cmdline_scan_candidate(name):
                continue

            try:
                cmdline = proc.cmdline()
            except psutil.AccessDenied:
                # We can see the pid (name() succeeded for the matcher
                # below to even be relevant) but cannot read argv. Surface
                # the pid with the unreadable flag so callers can dedupe
                # warnings rather than re-checking each scan tick.
                if _is_llama_server(name or "", []):
                    discovered.append(
                        ServerProcessInfo(
                            pid=proc.pid, port=None, alias=None,
                            model_path=None, create_time=None,
                            cmdline_unreadable=True,
                        )
                    )
                continue

            if not cmdline:
                continue

            if not _is_llama_server(name, cmdline):
                continue

            try:
                create_time = proc.create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                create_time = None

            discovered.append(
                ServerProcessInfo(
                    pid=proc.pid,
                    port=_extract_port_from_cmdline(cmdline),
                    alias=_extract_flag_value(cmdline, "--alias"),
                    model_path=(
                        _extract_flag_value(cmdline, "-m")
                        or _extract_flag_value(cmdline, "--model")
                    ),
                    create_time=create_time,
                    cmdline_unreadable=False,
                )
            )

        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue

    _PROCESS_SCAN_CACHE.set(_SCAN_KEY_ALL_SERVERS_ANNOTATED, discovered)
    return discovered


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

    # If model name provided, search for matching log files. Derive the
    # stem with the SAME mint start_server uses (log_stem_for): the files
    # on disk are stored under the sanitized-plus-hash stem, and a raw
    # glob would both miss the real file (``LFM2-350M-Pro.f16`` vs the
    # stored ``LFM2-350M-Pro_f16-<hash>``) and risk interpreting name
    # characters like ``[`` as glob metacharacters — the stem is
    # metachar-free by construction. When several match (a name reused
    # across ports), prefer the most recently written — glob order is
    # otherwise arbitrary (issue #145).
    if model_name is not None and port is None:
        match = _newest_log(LOG_DIR.glob(f"{log_stem_for(model_name)}-*.log"))
        if match is not None:
            return _tail_file(match, lines)

    if port:
        # Multiple models can leave a ``*-{port}.log`` behind across a swap;
        # the freshest belongs to the current occupant (issue #145).
        match = _newest_log(LOG_DIR.glob(f"*-{port}.log"))
        if match is not None:
            return _tail_file(match, lines)

    return []


def read_logs_for_port(port: int, lines: int = 100) -> list[str] | None:
    """Return the tail of the most-recent log file for ``port``.

    Resolves the freshest ``*-{port}.log`` in :data:`LOG_DIR` (mirroring
    the port-keyed glob in :func:`stream_logs`) and tails it, **without
    requiring a live process**. This is the read path for issue #201
    Part 2(b): a server that spawned then exited within ~1s leaves its
    death cause in ``logs/{stem}-{port}.log``, but the live-process lookup
    in :func:`stream_logs` (``pid=...``) can no longer reach it. The agent's
    ``GET /logs/{port}`` falls back here so the operator can still retrieve
    that log after the process is gone.

    Returns ``None`` when no log file exists for the port (the caller maps
    that to 404), otherwise the tailed lines — possibly an empty list when
    the newest file is empty, which is distinct from "no file at all."
    """
    match = _newest_log(LOG_DIR.glob(f"*-{port}.log"))
    if match is None:
        return None
    return _tail_file(match, lines)


def _tail_file(path: Path, lines: int) -> list[str]:
    """Read the last ``lines`` lines from ``path``.

    Bounded-tail implementation per ADR-LLNCH-013: reads at most a window of
    ``lines * _AVG_LOG_LINE_BYTES * 2`` bytes from the end of the file
    rather than slurping the whole file. With the default 100 lines and
    160 bytes/line that's a 32 KiB window, regardless of file size.

    The window is doubled past the heuristic so we still satisfy
    ``lines`` even when individual lines are unusually long — and if
    they're so long that even the doubled window underflows, we silently
    return what we found rather than escalating to a full read.

    **Caller contract (ADR-LLNCH-013 §Consequences):** ``len(result)`` may be
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
    process: subprocess.Popen | None = None,
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
            ``(False, last_logs)`` immediately. Per ADR-LLNCH-014 — used by
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
        process: Optional handle to the ``subprocess.Popen`` this call is
            waiting on. When given, each poll tick checks
            ``process.poll()`` first and fast-fails as soon as the child
            has exited — a process that crashed on launch (missing
            runtime lib, bad argv, no binary on this platform) would
            otherwise burn the *entire* ``timeout`` ceiling polling a
            port and log file that can never appear (#368). Callers that
            don't hold a live handle (e.g. waiting on an
            already-running, externally-launched server) simply omit it
            and get the previous poll-until-timeout behavior.

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
        # ADR-LLNCH-014: check cancel at the natural poll cadence — no new threads.
        if cancel_check is not None and cancel_check():
            last_logs = _attempt_logs(50) or last_logs
            return False, last_logs

        # #368: a process that has already exited will never bind the
        # port or write a "listening" line — polling it out to the full
        # timeout is pure burn. Fast-fail the instant the child is gone.
        if process is not None and process.poll() is not None:
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


def _listening_ports() -> set[int] | None:
    """Ports held by a TCP socket in the LISTEN state, or None.

    ``None`` means the socket table could not be read at all
    (``psutil.AccessDenied`` — macOS/BSD as non-root, hardened Linux
    without ``CAP_NET_ADMIN``); callers fall back to
    :func:`_bind_probe_port_in_use` for the single port they care about.

    Only LISTEN counts (#521, interacting with #518): a socket lingering
    in TIME_WAIT or CLOSE_WAIT after a stop does *not* occupy the port
    for a new server — reporting it as occupied is exactly the "port
    occupied" phantom the restart path keeps tripping over.
    """
    try:
        conns = psutil.net_connections(kind="tcp")
    except psutil.AccessDenied:
        return None
    return {
        conn.laddr.port
        for conn in conns
        if conn.status == psutil.CONN_LISTEN and conn.laddr
    }


def _bind_probe_port_in_use(port: int) -> bool:
    """AccessDenied fallback for :func:`is_port_in_use`: try to bind.

    Deliberately does *not* set ``SO_REUSEADDR`` — the probe must fail
    exactly when a real listener would fail to bind. ``EADDRINUSE``
    (``WSAEADDRINUSE`` 10048 on Windows) means occupied; a clean bind
    means free. Any other ``OSError`` (e.g. ``EACCES`` on a privileged
    port) is reported as occupied: we could not take the port, so we
    must not hand it out.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError as exc:
        if exc.errno in _EADDRINUSE_ERRNOS:
            return True
        logger.debug("Bind probe for port %d failed with %r; treating as in use", port, exc)
        return True
    finally:
        sock.close()
    return False


def is_port_in_use(port: int) -> bool:
    """Check if a port is currently held by a listening socket.

    Issue #521: previously this walked every process's argv looking for a
    ``--port``/``-p`` flag matching ``port`` — an every-process
    ``cmdline()`` scan (6.2s uncached on the Windows seat that motivated
    the fix). It now reads the OS socket table directly via
    ``psutil.net_connections(kind="tcp")`` (0.3ms measured) and checks
    whether any socket is **listening** on ``port``.

    This is a semantics change, not just a perf one: "port in use" now
    means "a socket is listening on it" rather than "some process's argv
    mentions it" — strictly more correct (it also catches a listener
    started with no ``--port`` flag, or a non-llama-server process
    squatting the port) and four orders of magnitude cheaper.

    Non-LISTEN states (TIME_WAIT, CLOSE_WAIT, ESTABLISHED client sockets
    whose local port happens to collide) are *not* occupancy: a fresh
    server binds over them. Counting them is the "port occupied"
    phantom #518 chases.

    Args:
        port: Port number to check.

    Returns:
        True if a TCP socket is listening on ``port``, False otherwise.
    """
    listening = _listening_ports()
    if listening is None:
        return _bind_probe_port_in_use(port)
    return port in listening
