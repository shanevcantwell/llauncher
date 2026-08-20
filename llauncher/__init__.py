"""Llauncher - MCP-first launcher for llama.cpp llama-server instances."""

from importlib.metadata import PackageNotFoundError, version

from dotenv import load_dotenv

# Load .env file from project root at package import time
load_dotenv()

# ONE-MINT: pyproject.toml's [project].version is the sole authority for the
# release version (ecosystem ground-physics constitution: ONE-MINT /
# IDENTITY⊥ENVELOPE — source every canonical name from its one authority, never
# re-declare it). Derive from installed package metadata rather than re-declaring
# a literal here, which drifts (#425). The PackageNotFoundError fallback covers
# running from source without an install (e.g. `pip install -e .` not yet run,
# or a raw checkout on PYTHONPATH).
try:
    __version__ = version("llauncher")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

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
