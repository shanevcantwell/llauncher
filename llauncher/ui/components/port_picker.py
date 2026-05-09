"""Reusable port picker for M4 start/swap flows (issue #50, stage 2).

ADR-010 moves port selection out of the model config and into the call
site of every verb. The UI's job is to (a) make the user supply a port
explicitly — no auto-allocation seam — and (b) surface the most likely
failure modes inline, *before* the user clicks the verb button, so they
can correct without paying a round-trip through ``operations.start``.

## Why no default seed

Earlier drafts of this slice considered seeding the input with the next
free port (``find_available_port(None)``). The user explicitly rejected
that: a pre-filled port nudges the user toward "just hit start", which
is exactly the auto-allocation behaviour ADR-010 deletes — only with
the seam moved one layer up. Keeping the input empty forces an
intentional choice every time, which is the architectural property the
ADR is paying for.

## Validation surface

The picker reports four states inline:

* **Blacklisted**: ``st.error`` + return ``None`` (block the verb).
* **In use by a managed peer**: ``st.warning`` + return the port. The
  downstream eviction dialog handles the "stop and swap" flow.
* **In use by an unmanaged process**: ``st.warning`` + return the port.
  The verb will fail with ``rejected_occupied``; the caption tells the
  user to expect that.
* **Free**: no caption, return the port.

## Non-goals

The picker is pure UI. It MUST NOT call ``find_available_port``,
``state.start_server``, or any ``operations.*`` verb — those are
side-effecting paths the verb button owns. Tests in
``tests/unit/test_port_picker.py`` assert this directly.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from llauncher.core.process import is_port_in_use
from llauncher.core.settings import BLACKLISTED_PORTS


def render_port_picker(
    state: Any,
    *,
    key_prefix: str,
    model_name: str | None = None,
) -> int | None:
    """Render a port input with inline validation.

    Args:
        state: The local :class:`LauncherState`. Used read-only — we
            inspect ``state.running`` to detect collisions with other
            llauncher-managed servers.
        key_prefix: Streamlit widget-key prefix. Each model card needs a
            unique prefix so its picker doesn't share state with its
            siblings (Streamlit dedupes by key).
        model_name: Name of the model the picker is for, or ``None`` if
            the picker is not bound to a specific model. When set, a
            collision on a port already serving *this same* model is
            ignored (the user is presumably re-rendering, not trying to
            swap into themselves).

    Returns:
        The chosen port, or ``None`` when the picker has nothing to
        return — either because the user has not typed yet, or because
        the typed value is invalid (blacklisted). A returned port may
        still trigger a downstream warning (in-use cases) — the caller
        is expected to fold those into the verb's eviction-dialog flow.
    """
    port = st.number_input(
        "Port",
        min_value=1024,
        max_value=65535,
        value=None,
        step=1,
        key=f"{key_prefix}_port",
        placeholder="Enter port",
        help="Required. ADR-010: the verb caller supplies the port; the model config does not.",
    )

    if port is None:
        # Nothing typed yet; no validation surface. The verb button
        # should be disabled until this returns a real int.
        return None

    port = int(port)

    if port in BLACKLISTED_PORTS:
        st.error(f"Port {port} is blacklisted. Pick another.")
        return None

    # Managed-peer collision: a different llauncher server is already
    # bound to this port. Surface the eviction handoff in the caption so
    # the user knows clicking start will prompt for swap.
    running_entry = state.running.get(port) if hasattr(state, "running") else None
    if running_entry is not None:
        existing_name = getattr(running_entry, "config_name", "unknown")
        if existing_name != model_name:
            st.warning(
                f"Port {port} in use by **{existing_name}**. "
                f"Starting will offer eviction."
            )
        return port

    # Unmanaged-process collision: something on the box owns this port,
    # but it isn't one of ours. ``operations.start`` will reject with
    # ``rejected_occupied``; warn the user proactively so they don't
    # interpret the error as a bug.
    if is_port_in_use(port):
        st.warning(
            f"Port {port} held by an unmanaged process; "
            f"start will fail with `rejected_occupied`."
        )
        return port

    # Free, valid, non-blacklisted. No caption.
    return port
