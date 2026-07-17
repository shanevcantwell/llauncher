"""Llauncher - MCP-first launcher for llama.cpp llama-server instances."""

from dotenv import load_dotenv

# Load .env file from project root at package import time
load_dotenv()

__version__ = "0.4.0a0"
__all__ = ["LauncherState"]


def __getattr__(name: str):
    """Lazily re-export ``LauncherState`` (PEP 562).

    ``llauncher.state`` transitively imports ``llauncher.core.settings``,
    which resolves ``LAUNCHER_STATE_DIR`` (and every path derived from it)
    at *module import time*. Importing it eagerly here — merely as a side
    effect of ``import llauncher`` — froze those paths before the CLI's
    ``--state-dir`` root callback (issue #215) ever got a chance to set
    the env var, defeating the override for every subcommand. No callers
    in this repo use the ``llauncher.LauncherState`` re-export (they use
    ``from llauncher.state import LauncherState`` directly); this keeps
    the attribute available for any external consumer without paying the
    eager-import cost on every ``import llauncher``.
    """
    if name == "LauncherState":
        from llauncher.state import LauncherState

        return LauncherState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
