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

# If the path is a directory, try to auto-detect llama-server binary
if _llama_server_path.is_dir():
    # Try llama-server first, then llama-server.exe (Windows)
    for candidate in ["llama-server", "llama-server.exe"]:
        binary_path = _llama_server_path / candidate
        if binary_path.exists():
            LLAMA_SERVER_PATH = binary_path
            break
    else:
        # Fallback: use the directory path (will fail later with FileNotFoundError)
        LLAMA_SERVER_PATH = _llama_server_path
else:
    LLAMA_SERVER_PATH = _llama_server_path

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

# API key for agent authentication (env: LAUNCHER_AGENT_TOKEN)
AGENT_API_KEY: str | None = os.getenv("LAUNCHER_AGENT_TOKEN")
if AGENT_API_KEY == "":
    AGENT_API_KEY = None

# Lockfile directory for running servers (per ADR-008).
# Configurable via env so container deployments can volume-mount it,
# enabling in-container agents to read host-side llauncher state.
LAUNCHER_RUN_DIR = Path(os.getenv(
    "LAUNCHER_RUN_DIR",
    str(Path.home() / ".llauncher" / "run"),
))

# Audit log path (per ADR-008). JSON Lines, append-only.
# Same volume-mount story as LAUNCHER_RUN_DIR.
LAUNCHER_AUDIT_PATH = Path(os.getenv(
    "LAUNCHER_AUDIT_PATH",
    str(Path.home() / ".llauncher" / "audit.jsonl"),
))

# Per-server log directory (ADR-013). Files inside are
# ``{name}-{port}.log`` plus rotated siblings ``{name}-{port}.log.{N}``.
# Configurable via env so container deployments can volume-mount it the
# same way as ``LAUNCHER_RUN_DIR`` and ``LAUNCHER_AUDIT_PATH``.
LAUNCHER_LOG_DIR = Path(os.getenv(
    "LAUNCHER_LOG_DIR",
    str(Path.home() / ".llauncher" / "logs"),
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

# TTL in seconds for the ``/footer-context/{port}`` per-port cache
# (ADR-012). Default 1.0 absorbs footer poll cadence (multiple
# redraws per second collapse into one lockfile + ConfigStore read).
# ``<= 0`` disables caching — every request hits disk.
LAUNCHER_FOOTER_CACHE_S = float(os.getenv("LAUNCHER_FOOTER_CACHE_S", "1.0"))
