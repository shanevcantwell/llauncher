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

# Default port for new models
DEFAULT_PORT = int(os.getenv("DEFAULT_PORT", "8080"))

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
