"""Console-script entry point for the Streamlit dashboard.

Exists so operators launch the UI with a single command (``llauncher-ui``)
instead of the ``cd <repo> && scripts/run.sh ui`` ritual. Mirrors the
``llauncher-agent`` / ``llauncher-mcp`` entry-point convention in
``pyproject.toml`` ``[project.scripts]``.

Launches Streamlit in a subprocess (``python -m streamlit run app.py``)
rather than importing it: ``ui/`` is an endpoint-layer sibling, and
shelling out keeps this free of any cross-layer Python import (see
``.claude/architecture.md``) and stable across Streamlit's internal CLI
module reshuffles.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

# The dashboard ships with no built-in auth (security-hardening §2.8 / C12),
# so the default bind is loopback. Operators opt into LAN exposure
# explicitly via LLAUNCHER_UI_HOST, and only behind a gateway
# (Tailscale / SSH tunnel / authenticating reverse proxy).
DEFAULT_UI_HOST = "127.0.0.1"

# Two-L spelling per the project name; scripts/run.sh and run.bat read
# the same name (#151), so all UI entrances agree.
UI_HOST_ENV = "LLAUNCHER_UI_HOST"


def resolve_ui_host(environ: Mapping[str, str] | None = None) -> str:
    """Return the UI bind address, defaulting to loopback.

    An empty or unset ``LLAUNCHER_UI_HOST`` falls back to loopback so a
    blank export never silently publishes the no-auth dashboard.
    """
    env = os.environ if environ is None else environ
    return env.get(UI_HOST_ENV) or DEFAULT_UI_HOST


def build_streamlit_argv(app_path: Path, host: str) -> list[str]:
    """Assemble the ``python -m streamlit run`` command line."""
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        host,
    ]


def main() -> int:
    """Launch the Streamlit dashboard. Returns Streamlit's exit code."""
    try:
        import streamlit  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "Streamlit is not installed. Install the UI extra:\n"
            '    pip install "llauncher[ui]"\n'
        )
        return 1

    app_path = Path(__file__).resolve().parent / "app.py"
    argv = build_streamlit_argv(app_path, resolve_ui_host())
    return subprocess.call(argv)


if __name__ == "__main__":
    raise SystemExit(main())
