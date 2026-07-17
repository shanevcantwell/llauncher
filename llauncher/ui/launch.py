"""Console-script entry point for the Streamlit dashboard.

Exists so operators launch the UI with a single command (``llauncher-ui``)
instead of the ``cd <repo> && scripts/run.sh ui`` ritual. Mirrors the
``llauncher-agent`` / ``llauncher-mcp`` entry-point convention in
``pyproject.toml`` ``[project.scripts]``.

Launches Streamlit in a subprocess (``python -m streamlit run app.py``)
rather than importing it: ``ui/`` is an endpoint-layer sibling, and
shelling out keeps this free of any cross-layer Python import (see
``docs/ARCHITECTURE.md``) and stable across Streamlit's internal CLI
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

# NB: deliberately the two-L spelling. scripts/run.sh still reads the
# legacy single-L LAUNCHER_UI_HOST; that drift is tracked for rename so
# both converge on LLAUNCHER_UI_HOST.
UI_HOST_ENV = "LLAUNCHER_UI_HOST"

# Streamlit's own default; made explicit here so the day 8501 collides with
# another service, LLAUNCHER_UI_PORT is a documented knob rather than a
# hardcoded-by-omission surprise (#356).
DEFAULT_UI_PORT = 8501

UI_PORT_ENV = "LLAUNCHER_UI_PORT"


def resolve_ui_host(environ: Mapping[str, str] | None = None) -> str:
    """Return the UI bind address, defaulting to loopback.

    An empty or unset ``LLAUNCHER_UI_HOST`` falls back to loopback so a
    blank export never silently publishes the no-auth dashboard.
    """
    env = os.environ if environ is None else environ
    return env.get(UI_HOST_ENV) or DEFAULT_UI_HOST


def resolve_ui_port(environ: Mapping[str, str] | None = None) -> int:
    """Return the UI bind port, defaulting to Streamlit's 8501.

    An empty or unset ``LLAUNCHER_UI_PORT`` falls back to the default —
    absence is not garbage. A set value that fails to parse as an integer,
    or that parses outside 1-65535, fails loud (PARSE-AT-THE-DOOR) rather
    than silently falling back — garbage is not absence.
    """
    env = os.environ if environ is None else environ
    raw = env.get(UI_PORT_ENV)
    if not raw:
        return DEFAULT_UI_PORT

    try:
        port = int(raw)
    except ValueError:
        raise ValueError(
            f"Invalid {UI_PORT_ENV}={raw!r}: must be an integer in 1-65535"
        ) from None

    if not 1 <= port <= 65535:
        raise ValueError(
            f"Invalid {UI_PORT_ENV}={raw!r}: must be an integer in 1-65535"
        )

    return port


def build_streamlit_argv(app_path: Path, host: str, port: int) -> list[str]:
    """Assemble the ``python -m streamlit run`` command line."""
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        host,
        "--server.port",
        str(port),
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

    try:
        port = resolve_ui_port()
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    app_path = Path(__file__).resolve().parent / "app.py"
    argv = build_streamlit_argv(app_path, resolve_ui_host(), port)
    return subprocess.call(argv)


if __name__ == "__main__":
    raise SystemExit(main())
