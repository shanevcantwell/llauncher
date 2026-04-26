"""Application settings loaded from environment variables.

This module provides centralized access to configuration that can be
overridden via environment variables or the .env file.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


# Path to llama-server binary
LLAMA_SERVER_PATH = Path(os.getenv(
    "LLAMA_SERVER_PATH",
    str(Path.home() / ".local" / "bin" / "llama-server")
))

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
