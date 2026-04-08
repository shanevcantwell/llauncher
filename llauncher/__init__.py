"""Llauncher - MCP-first launcher for llama.cpp llama-server instances."""

from dotenv import load_dotenv

# Load .env file from project root at package import time
load_dotenv()

from llauncher.state import LauncherState

__version__ = "0.1.0"
__all__ = ["LauncherState"]
