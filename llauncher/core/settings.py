"""Application settings loaded from environment variables.

This module provides centralized access to configuration that can be
overridden via environment variables or the .env file.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


# Path to llama-server binary
_llama_server_path = Path(os.getenv(
    "LLAMA_SERVER_PATH",
    str(Path.home() / ".local" / "bin" / "llama-server")
))

# If the path is a directory, try to auto-detect llama-server binary.
#
# Every filesystem probe here (`.is_dir()`, `.exists()`) is guarded
# against OSError (incl. PermissionError / FileNotFoundError on the
# parent). A stale, unreadable, or migrated LLAMA_SERVER_PATH must never
# raise at *import* time — that would brick the entire package (issue
# #195: `import llauncher`, `llauncher --help`, and test collection all
# fail). On any probe failure we fall back to the configured path as a
# plain Path — the same graceful fallback the happy path already takes
# when the path isn't a directory — so the failure surfaces with a clear
# message at point-of-use (start/preflight), not at import.
def _resolve_llama_server_path(configured: Path) -> Path:
    try:
        is_dir = configured.is_dir()
    except OSError:
        logger.warning(
            "Could not probe LLAMA_SERVER_PATH %r (%s); deferring "
            "validation to start/preflight.",
            str(configured),
            "unreadable",
        )
        return configured
    if not is_dir:
        return configured
    # Directory: try llama-server first, then llama-server.exe (Windows).
    for candidate in ["llama-server", "llama-server.exe"]:
        binary_path = configured / candidate
        try:
            exists = binary_path.exists()
        except OSError:
            continue
        if exists:
            return binary_path
    # Fallback: use the directory path (will fail later with a clear error).
    return configured


LLAMA_SERVER_PATH = _resolve_llama_server_path(_llama_server_path)

# Path to launch scripts directory
SCRIPTS_PATH = Path(os.getenv(
    "SCRIPTS_PATH",
    str(Path.home() / ".local" / "bin")
))

# Default port for new models. Used as the *seed* of the port-scan range
# in :func:`llauncher.core.process.find_available_port` — not as an
# auto-allocation fallback (ADR-010 / issue #58 require explicit ports at
# every API boundary). Default 8081 because 8080 collides with common
# local services (searxng, Apache fronts, …) and is the default of
# ``BLACKLISTED_PORTS`` per ``.env.example``. Override via the
# ``DEFAULT_PORT`` env var or ``.env``.
DEFAULT_PORT = int(os.getenv("DEFAULT_PORT", "8081"))

# Blacklisted ports (comma-separated)
_BLACKLISTED_PORTS_RAW = os.getenv("BLACKLISTED_PORTS", "")
if _BLACKLISTED_PORTS_RAW:
    BLACKLISTED_PORTS = [
        int(p.strip()) for p in _BLACKLISTED_PORTS_RAW.split(",")
        if p.strip().isdigit()
    ]
else:
    BLACKLISTED_PORTS = []

# Log level
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# API key for agent authentication (env: LLAUNCHER_AGENT_TOKEN)
AGENT_API_KEY: str | None = os.getenv("LLAUNCHER_AGENT_TOKEN")
if AGENT_API_KEY == "":
    AGENT_API_KEY = None

# Port the local llauncher agent binds (env: LLAUNCHER_AGENT_PORT, default
# 8765). Mirrors ``agent.config.AgentConfig.from_env``'s port read so the
# delegation gate (``core.delegation``) and the remote-node client share a
# single source of truth rather than each re-reading the env inline. Only
# the ``LLAUNCHER_``-prefixed name is honored — the legacy single-``L``
# spelling is intentionally NOT a fallback (issue #151 naming direction;
# see test_env_var_naming_regression).
AGENT_PORT = int(os.getenv("LLAUNCHER_AGENT_PORT", "8765"))

# Base directory for all durable launcher state (issue #196). Every
# per-actor state path (config, run lockfiles, audit log, per-server
# logs, node registry + token sidecars, agent token) derives from this
# single base so a multiuser deployment can point every actor at a
# shared, non-home-relative location via one env var. Each per-dir env
# override below still wins when set explicitly; otherwise the path is
# derived from this base. The legacy ``~/.llauncher`` default is the
# base's default, so with ``LAUNCHER_STATE_DIR`` unset every resolved
# path is byte-identical to before this var existed. Pure getenv + Path
# — no filesystem probing at import (cf. issue #195).
LAUNCHER_STATE_DIR = Path(os.getenv(
    "LAUNCHER_STATE_DIR",
    str(Path.home() / ".llauncher"),
))

# Lockfile directory for running servers (per ADR-008).
# Configurable via env so container deployments can volume-mount it,
# enabling in-container agents to read host-side llauncher state.
# Precedence: explicit ``LAUNCHER_RUN_DIR`` > ``LAUNCHER_STATE_DIR``/run.
LAUNCHER_RUN_DIR = Path(os.getenv(
    "LAUNCHER_RUN_DIR",
    str(LAUNCHER_STATE_DIR / "run"),
))

# Audit log path (per ADR-008). JSON Lines, append-only.
# Same volume-mount story as LAUNCHER_RUN_DIR.
# Precedence: explicit ``LAUNCHER_AUDIT_PATH`` > ``LAUNCHER_STATE_DIR``/audit.jsonl.
LAUNCHER_AUDIT_PATH = Path(os.getenv(
    "LAUNCHER_AUDIT_PATH",
    str(LAUNCHER_STATE_DIR / "audit.jsonl"),
))

# Per-server log directory (ADR-013). Files inside are
# ``{stem}-{port}.log`` plus rotated siblings ``{stem}-{port}.log.{N}``,
# where ``stem`` is minted by ``core.process.log_stem_for`` (#63/#146).
# Configurable via env so container deployments can volume-mount it the
# same way as ``LAUNCHER_RUN_DIR`` and ``LAUNCHER_AUDIT_PATH``.
LAUNCHER_LOG_DIR = Path(os.getenv(
    "LAUNCHER_LOG_DIR",
    str(LAUNCHER_STATE_DIR / "logs"),
))

# Size cap for a single live log file before rotation kicks in (ADR-013).
# Default 50 MiB; ``<= 0`` disables rotation entirely.
LAUNCHER_LOG_MAX_BYTES = int(os.getenv(
    "LAUNCHER_LOG_MAX_BYTES",
    str(50 * 1024 * 1024),
))

# How many rotated log files to retain alongside the live file
# (ADR-013). With the default 3, the on-disk set is
# ``foo-8081.log`` plus ``foo-8081.log.{1,2,3}``.
LAUNCHER_LOG_KEEP = int(os.getenv("LAUNCHER_LOG_KEEP", "3"))

# Graceful-shutdown grace periods for terminating a llama-server
# (issue #140). ``core.process.stop_server_by_pid`` sends SIGTERM and
# waits up to LLAUNCHER_STOP_CHILD_GRACE_S for the process's children,
# then up to LLAUNCHER_STOP_GRACE_S for the main process, before
# escalating to SIGKILL. Worst-case blocking time for a synchronous
# stop is the sum (~8 s at the defaults). Env-tunable so deployments
# with slow GPU unloads can lengthen the grace and test profiles can
# shorten it without real-time sleeps. ``LLAUNCHER_*`` prefix per the
# #151 naming direction — these were never released under the legacy
# single-L prefix, so no backcompat alias exists.
LLAUNCHER_STOP_CHILD_GRACE_S = float(os.getenv("LLAUNCHER_STOP_CHILD_GRACE_S", "3.0"))
LLAUNCHER_STOP_GRACE_S = float(os.getenv("LLAUNCHER_STOP_GRACE_S", "5.0"))

# TTL in seconds for the ``/footer-context/{port}`` per-port cache
# (ADR-012). Default 1.0 absorbs footer poll cadence (multiple
# redraws per second collapse into one lockfile + ConfigStore read).
# ``<= 0`` disables caching — every request hits disk.
LAUNCHER_FOOTER_CACHE_S = float(os.getenv("LAUNCHER_FOOTER_CACHE_S", "1.0"))
