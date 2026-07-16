"""Shared Streamlit ``AppTest`` harness for ``llauncher.ui`` tab tests (#69).

The UI tabs are plain ``render_*(…facades…)`` functions that talk to the
backend only through the engine (``state`` / ``operations``) and to remote
nodes only through ``remote/`` (per ``docs/ARCHITECTURE.md`` and ADR-025).
That shape makes them headlessly testable: drive a single tab with
``streamlit.testing.v1.AppTest`` while the engine facades are **mocked**, then
assert on the rendered element tree *and* on which facade methods the tab
called.

The harness deliberately does **not** mock ``streamlit`` itself — AppTest sets
up a real ScriptRunContext so the tab's own ``import streamlit as st`` calls are
captured as elements (``at.header``, ``at.button``, ``at.error``, …). Only the
downward facades are doubles. This is what lets a behavioral test assert that
all node I/O went *through* ``remote/`` and that no direct HTTP escaped the UI
(see ``forbid_direct_http``), the runtime complement to the static import guard
in ``tests/architecture/test_ui_layer_boundaries.py``.

Usage::

    def test_dashboard_empty(tab_harness, mock_state, mock_registry,
                             mock_aggregator):
        at = tab_harness(render_dashboard, mock_state, mock_registry,
                         mock_aggregator, "local")
        assert not at.exception
        assert "No servers running" in at.info[0].value
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from llauncher.core import audit_log
from llauncher.remote.node import NodeStatus


# ---------------------------------------------------------------------------
# The script body AppTest executes. ``AppTest.from_function`` reads this
# function's *source*, dedents it, and appends ``_tab_script(*__args,
# **__kwargs)``. We pass the render callable and its (mocked-facade) args via
# ``kwargs``, so the only thing that runs under the ScriptRunContext is the one
# tab under test. Keep the body import-free: the render callable is already
# imported in the test process and brings its own ``import streamlit as st``.
# ---------------------------------------------------------------------------
def _tab_script(render, render_args):  # pragma: no cover - exec'd by AppTest
    render(*render_args)


@pytest.fixture
def tab_harness():
    """Return a callable that renders one tab headlessly via ``AppTest``.

    ``tab_harness(render_callable, *facade_args, run=True, default_timeout=6)``
    builds an ``AppTest`` whose script is a single call to ``render_callable``
    with the supplied (typically mocked) facades, runs it once by default, and
    returns the ``AppTest`` for assertions / further interaction
    (``at.button[0].click(); at.run()``).
    """

    def _render(render_callable, *facade_args, run=True, default_timeout=6):
        at = AppTest.from_function(
            _tab_script,
            default_timeout=default_timeout,
            kwargs={"render": render_callable, "render_args": facade_args},
        )
        if run:
            at.run()
        return at

    return _render


# ---------------------------------------------------------------------------
# Mocked engine facades. These are doubles for the *downward* dependencies a
# tab is allowed to use; the tab never reaches a node except through them.
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_state():
    """A ``LauncherState`` double with empty running/models collections."""
    state = MagicMock(name="LauncherState")
    state.running = {}
    state.models = {}
    return state


@pytest.fixture
def mock_aggregator():
    """A ``RemoteAggregator`` double returning no remote servers/models."""
    agg = MagicMock(name="RemoteAggregator")
    agg.get_all_servers.return_value = []
    agg.get_all_models.return_value = {}
    return agg


def make_audit_entry(
    *,
    action: audit_log.AuditAction = audit_log.AuditAction.STARTED,
    result: audit_log.AuditResult = audit_log.AuditResult.SUCCESS,
    caller: str = "ui",
    port: int | None = 8000,
    model: str | None = "test-model",
    message: str = "",
    timestamp: str = "2026-07-16T00:00:00+00:00",
) -> audit_log.AuditEntry:
    """Build a real ``AuditEntry`` for local-path audit-tab tests.

    ``ui/tabs/audit.py``'s local branch reads ``AuditEntry`` objects (not
    dicts) straight from ``core.audit_log.read_entries`` and calls
    ``.action.value`` / ``.result.value`` on them, so the double here is a
    real dataclass instance rather than a ``MagicMock`` — cheaper to build
    correctly and it exercises the same enum-coercion path production does.
    """
    return audit_log.AuditEntry(
        timestamp=timestamp,
        action=action,
        result=result,
        caller=caller,
        port=port,
        model=model,
        message=message,
    )


@pytest.fixture
def make_entry():
    """Expose :func:`make_audit_entry` as a fixture-style factory."""
    return make_audit_entry


@pytest.fixture
def mock_read_entries():
    """Patch ``llauncher.core.audit_log.read_entries`` for the audit tab's
    local-target dispatch path (``ui/tabs/audit.py`` calls it directly, with
    no HTTP hop — see the module docstring's "ADR-013 hook" / "Scope"
    sections).

    Returns the ``MagicMock`` standing in for ``read_entries`` so a test can
    set ``.return_value`` (a list of :func:`make_audit_entry` results) and
    later assert on ``.call_args`` (e.g. the ``limit=`` the tab forwarded).
    Defaults to an empty list — the tab's "no entries yet" branch.
    """
    with patch("llauncher.core.audit_log.read_entries") as mock_fn:
        mock_fn.return_value = []
        yield mock_fn


def make_remote_node(
    name: str = "gpu-rig",
    host: str = "192.168.1.50",
    port: int = 8765,
    status: NodeStatus = NodeStatus.ONLINE,
    *,
    online: bool = True,
    node_info: dict | None = None,
    error_message: str | None = None,
    read_audit_result: list[dict] | None = None,
):
    """Build a ``RemoteNode`` double for registry-backed tab tests.

    The double answers the surface ``ui/tabs/nodes.py`` touches: ``name`` /
    ``host`` / ``port`` / ``timeout`` / ``status`` / ``last_seen`` /
    ``_error_message`` plus the I/O verbs ``ping`` and ``get_node_info``.
    Because it is a mock, *no real HTTP happens* — which is the point: the tab's
    node I/O is observed at this seam, not on the wire.

    ``read_audit_result`` seeds the ``RemoteNode.read_audit`` return value for
    ``ui/tabs/audit.py``'s remote dispatch path (issue #64): pass a list of
    ``AuditEntry.to_dict()``-shaped dicts for a successful read, or leave the
    default ``None`` to model "node offline or unreachable" (the tab's own
    unreachable branch — see ``ui/tabs/audit.py::render_audit_tab``).
    """
    node = MagicMock(name=f"RemoteNode[{name}]")
    node.name = name
    node.host = host
    node.port = port
    node.timeout = 5
    node.status = status
    node.last_seen = None
    node._error_message = error_message
    node.ping.return_value = online
    node.get_node_info.return_value = node_info or {
        "os": "Linux",
        "python_version": "3.12.3",
        "ip_addresses": [host],
    }
    node.read_audit.return_value = read_audit_result
    return node


@pytest.fixture
def make_node():
    """Expose :func:`make_remote_node` as a fixture-style factory."""
    return make_remote_node


def make_registry(nodes=()):
    """Build an iterable ``NodeRegistry`` double seeded with ``nodes``.

    ``ui/tabs/nodes.py`` iterates the registry (``for node in registry``) and
    calls ``refresh_all`` / ``add_node`` / ``remove_node`` / ``get_node``. The
    ``__iter__`` side-effect yields a *fresh* iterator each pass so AppTest
    reruns don't exhaust it.
    """
    nodes = list(nodes)
    registry = MagicMock(name="NodeRegistry")
    registry.__iter__.side_effect = lambda: iter(nodes)
    registry.__len__.return_value = len(nodes)
    registry.add_node.return_value = (True, "Node added")
    registry.remove_node.return_value = (True, "Node removed")
    registry.get_node.side_effect = lambda n: next(
        (nd for nd in nodes if nd.name == n), None
    )
    return registry


@pytest.fixture
def mock_registry():
    """An empty ``NodeRegistry`` double (truthy, iterates to nothing)."""
    return make_registry([])


@pytest.fixture
def registry_factory():
    """Expose :func:`make_registry` as a fixture-style factory."""
    return make_registry


# ---------------------------------------------------------------------------
# ``ConfigStore`` mock (SP-4, #328): the add/edit-model forms in
# ``ui/tabs/forms.py`` are the "no orchestration verb" special case flagged
# by the #330 parity audit — they call ``ConfigStore.add_model`` /
# ``update_model`` / ``load`` directly rather than through an ``ops.*``
# verb. Patch those three classmethods where ``forms.py`` looks them up
# (its own module attribute, since it does ``from llauncher.core.config
# import ConfigStore`` at import time) so tests observe exactly the calls
# the form makes without touching the real on-disk config store.
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_config_store():
    """Patch ``ConfigStore.add_model``/``update_model``/``load`` for forms.py.

    Yields the ``ConfigStore`` class (patched in place) so tests can both
    drive return values (e.g. ``mock_config_store.load.return_value = {...}``)
    and assert on call args (``mock_config_store.add_model.assert_called_once_with(...)``).
    ``load`` defaults to an empty dict — the common "brand new model" shape.
    """
    with patch(
        "llauncher.ui.tabs.forms.ConfigStore.add_model"
    ) as add_model, patch(
        "llauncher.ui.tabs.forms.ConfigStore.update_model"
    ) as update_model, patch(
        "llauncher.ui.tabs.forms.ConfigStore.load"
    ) as load:
        load.return_value = {}
        from llauncher.core.config import ConfigStore

        yield ConfigStore


# ---------------------------------------------------------------------------
# Runtime complement to the static import guard: prove that rendering a tab
# opens no real network transport. If the UI ever regressed to doing its own
# HTTP (instead of going through the mocked ``remote/`` facade), one of these
# patched entry points would fire and fail the test.
# ---------------------------------------------------------------------------
_DIRECT_HTTP_TARGETS = (
    "socket.socket.connect",
    "socket.socket.connect_ex",
    "socket.create_connection",
)


@contextmanager
def _forbid_direct_http():
    def _boom(*_args, **_kwargs):  # pragma: no cover - only fires on violation
        raise AssertionError(
            "ui/ attempted direct network I/O during render — node I/O must "
            "go through remote/ (NodeRegistry / RemoteNode / RemoteAggregator), "
            "never a raw socket. See docs/ARCHITECTURE.md / ADR-025."
        )

    patches = [patch(target, side_effect=_boom) for target in _DIRECT_HTTP_TARGETS]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


@pytest.fixture
def forbid_direct_http():
    """Context manager asserting no raw socket connect happens inside it.

    Use around a ``tab_harness(...)`` call to assert at runtime that the tab's
    node I/O stayed inside the mocked ``remote/`` seam and never reached the
    real network — the behavioral complement to the static guard.
    """
    return _forbid_direct_http
