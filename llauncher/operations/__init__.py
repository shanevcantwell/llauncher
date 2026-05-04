"""Tool-layer operations for the v2 architecture.

Per ADR-008 (LauncherState as Stateless Facade) and ADR-010 (port at the
call site). Stateless service functions that compose the core
infrastructure modules — :mod:`llauncher.core.config` (ConfigStore),
:mod:`llauncher.core.lockfile`, :mod:`llauncher.core.marker`,
:mod:`llauncher.core.audit_log`, and :mod:`llauncher.core.process` —
into the public operations the CLI, HTTP Agent, and MCP server expose.

Each operation:

- Reads from external sources of truth (``config.json``, lockfile dir,
  process table) on every call — no cached state.
- Writes lockfile and audit-log entries as commanded actions occur.
- Returns a structured result with the ADR-010 ``action`` envelope.

This package was extracted from a single ``operations.py`` file during
M2 to keep each verb's implementation focused (file-size hygiene per the
project's coding-style guidance). The public surface is preserved:
existing callers ``from llauncher import operations as ops`` and use
``ops.start``, ``ops.stop``, ``ops.swap``, etc.

The ``proc``, ``lf``, ``al``, ``mk``, and ``ConfigStore`` symbols are
re-exported at the package level so that :func:`unittest.mock.patch`
calls of the form ``patch("llauncher.operations.proc.start_server", ...)``
continue to work — they mutate attributes on the underlying core modules,
which the verb sub-modules also reach through these aliases.
"""

from __future__ import annotations

# Re-exported core-module aliases. These aliases are part of the package
# public surface — tests patch through ``llauncher.operations.<alias>.*``.
from llauncher.core import audit_log as al  # noqa: F401
from llauncher.core import lockfile as lf  # noqa: F401
from llauncher.core import marker as mk  # noqa: F401
from llauncher.core import process as proc  # noqa: F401
from llauncher.core.config import ConfigStore  # noqa: F401

from .preflight import (
    default_model_health_check,
    default_vram_check,
    estimate_vram_mb,
)
from .start import StartResult, start
from .stop import StopResult, stop
from .swap import (
    DEFAULT_READINESS_TIMEOUT_S,
    PreflightCheck,
    STARTUP_LOG_TAIL_MAX,
    SwapResult,
    swap,
)

__all__ = [
    # Verbs
    "start",
    "stop",
    "swap",
    # Result envelopes
    "StartResult",
    "StopResult",
    "SwapResult",
    # Swap-related constants and types
    "PreflightCheck",
    "STARTUP_LOG_TAIL_MAX",
    "DEFAULT_READINESS_TIMEOUT_S",
    # Pre-flight adapters (callable seams; pass to swap() to override)
    "default_model_health_check",
    "default_vram_check",
    "estimate_vram_mb",
]
