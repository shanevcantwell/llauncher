"""Reusable target-node selector for M4 tabs (issue #48 / m4-design Slice 11).

Every M4 tab that targets a *single* node — start, swap, stop, delete,
the audit-tail tab, etc. — needs to ask the user "which node?". This
module is the single answer.

## Design

The component is a thin wrapper over ``st.selectbox`` plus a pure-logic
helper, :func:`compute_node_options`, that derives the option list from
a :class:`NodeRegistry`. Splitting the two lets us unit-test the option
derivation without spinning up Streamlit.

The selection is persisted to ``st.session_state[key]`` (default
``"ui.target_node"``) so the choice survives reruns and is visible to
*other* tabs without those tabs needing to know how the selection was
made. This is the contract m4-design Slice 11 prescribes; later slices
(#50 tab restructure) consume the same key.

## "local" handling

:class:`NodeRegistry` does not synthesize a "local" entry — it's a
collection of *peer* nodes loaded from ``nodes.json``. The local agent
is implicit. This component synthesizes ``"local"`` as the first
option, regardless of registry contents (including an empty registry —
"local" is always selectable so the UI never shows a meaningless empty
selector).

If a peer is also literally named ``"local"`` the synthetic entry wins
(deduplicated). That's the user's collision to fix in their config.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

import streamlit as st


# Public session-state key. Importable by other modules that want to
# *read* the current selection without re-rendering the selector.
TARGET_NODE_KEY: str = "ui.target_node"

# Constant for the synthetic "local" entry. Exposed so callers can
# compare against this rather than re-typing the string literal.
LOCAL_NODE: str = "local"


@runtime_checkable
class _Named(Protocol):
    """Minimal duck-type the option builder needs from registry items.

    A real :class:`NodeRegistry` yields :class:`RemoteNode` instances
    that have a ``.name`` attribute; tests sometimes pass plain
    ``SimpleNamespace`` doubles. Stating the requirement as a
    :class:`Protocol` is more honest than a bare ``Iterable`` (which
    accepts ``str``, ``dict``, and other surprising shapes).
    """

    name: str


def compute_node_options(registry: Iterable[_Named]) -> list[str]:
    """Return the ordered selectbox options for ``registry``.

    Pure logic — no Streamlit dependency. ``"local"`` is always the
    first entry; remote peers follow in registry-iteration order, with
    any peer also named ``"local"`` deduplicated against the synthetic
    entry.

    Accepts any iterable of objects matching :class:`_Named` (the
    ``.name`` attribute is the only protocol surface). A real
    :class:`NodeRegistry` qualifies; ``SimpleNamespace`` test doubles
    qualify; a bare list of strings does not (by design — strings have
    no ``.name`` attribute, and silently treating them as node names
    would mask a wiring bug).
    """
    options: list[str] = [LOCAL_NODE]
    seen: set[str] = {LOCAL_NODE}
    for node in registry:
        name = getattr(node, "name", None)
        if name is None or name in seen:
            continue
        options.append(name)
        seen.add(name)
    return options


def render_node_selector(
    registry: Iterable[_Named],
    *,
    key: str = TARGET_NODE_KEY,
    label: str = "Target node",
    help: str | None = "Choose which node operations target.",
) -> str:
    """Render a node-selector selectbox and return the selected node name.

    The selection is persisted to ``st.session_state[key]`` automatically
    by Streamlit's stateful selectbox. ``"local"`` is always the first
    option (see module docstring).

    Args:
        registry: Source of remote peer names.
        key: Session-state key under which the selection is persisted.
            Defaults to :data:`TARGET_NODE_KEY` so independent tabs
            stay in sync without coordination.
        label: Selectbox label shown to the user.
        help: Tooltip shown next to the label. Pass ``None`` to omit.

    Returns:
        The currently-selected node name. ``"local"`` when nothing has
        been chosen yet (the first option is the implicit default).
    """
    options = compute_node_options(registry)
    return st.selectbox(
        label,
        options=options,
        key=key,
        help=help,
    )
