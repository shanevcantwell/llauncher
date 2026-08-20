"""Tests for :mod:`llauncher.core.node_info` (issue #367).

``get_node_name`` must fall back to the OS hostname both when
``LLAUNCHER_AGENT_NODE_NAME`` is absent *and* when it is present but
empty (e.g. an installer-written ``VAR=`` env-block entry). An
``os.getenv(name, default)`` call only applies its default when the
variable is absent — a present-and-empty value is returned as-is,
defeating the fallback. The fix is a falsy-or: ``os.getenv(name) or
default``.
"""

from __future__ import annotations

import socket

from llauncher.core import node_info


def test_get_node_name_absent_env_falls_back_to_hostname(monkeypatch):
    monkeypatch.delenv("LLAUNCHER_AGENT_NODE_NAME", raising=False)

    assert node_info.get_node_name() == socket.gethostname()


def test_get_node_name_empty_env_falls_back_to_hostname(monkeypatch):
    """Present-but-empty must not defeat the hostname fallback (#367)."""
    monkeypatch.setenv("LLAUNCHER_AGENT_NODE_NAME", "")

    assert node_info.get_node_name() == socket.gethostname()


def test_get_node_name_set_env_is_honored(monkeypatch):
    monkeypatch.setenv("LLAUNCHER_AGENT_NODE_NAME", "custom-node")

    assert node_info.get_node_name() == "custom-node"


def test_get_node_info_empty_env_reports_hostname_as_node_name(monkeypatch):
    """The full payload (served over /node-info) must reflect the fallback too."""
    monkeypatch.setenv("LLAUNCHER_AGENT_NODE_NAME", "")

    payload = node_info.get_node_info()

    assert payload["node_name"] == socket.gethostname()
    assert payload["node_name"] == payload["hostname"]
