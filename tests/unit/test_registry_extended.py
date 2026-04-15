"""Extended tests for the NodeRegistry module."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llauncher.remote.node import RemoteNode, NodeStatus
from llauncher.remote.registry import NodeRegistry, NODES_FILE


class TestIsLocalAgentReady:
    """Tests for NodeRegistry.is_local_agent_ready method."""

    def test_is_local_agent_ready_with_existing_online_node(self, monkeypatch):
        """Test when local node exists and is online."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()

        # Add a local node
        registry.add_node("local", "localhost", 8765)
        local_node = registry.get_node("local")
        assert local_node is not None
        assert local_node.status == NodeStatus.OFFLINE

        # Mock get_node to return the local node with ONLINE status
        original_get_node = registry.get_node

        def mock_get_node(name):
            node = original_get_node(name)
            if node:
                node.status = NodeStatus.ONLINE
            return node

        monkeypatch.setattr(registry, "get_node", mock_get_node)

        # Mock ping to return True
        monkeypatch.setattr(RemoteNode, "ping", lambda self: True)

        # Also mock socket to avoid actual connection attempts
        def mock_socket_class(family, type_):
            s = MagicMock()
            s.connect = MagicMock(side_effect=ConnectionRefusedError("Not actually connecting"))
            return s

        monkeypatch.setattr("socket.socket", mock_socket_class)

        result = registry.is_local_agent_ready()

        # Should return True when node exists and ping succeeds
        assert result is True

    def test_is_local_agent_ready_socket_success(self, monkeypatch):
        """Test when socket connection succeeds but node doesn't exist in registry."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()

        # Mock socket to simulate successful connection
        mock_socket = MagicMock()
        mock_socket.connect.return_value = None

        def mock_socket_class(family, type_):
            return mock_socket

        monkeypatch.setattr("socket.socket", mock_socket_class)

        # Mock add_node to track calls
        added = []

        def mock_add_node(name, host, port, overwrite=False):
            added.append((name, host, port))
            return True, "Added"

        monkeypatch.setattr(registry, "add_node", mock_add_node)

        # Mock get_node to return None (node not in registry yet)
        def mock_get_node(name):
            return None

        monkeypatch.setattr(registry, "get_node", mock_get_node)

        # Mock ping to return True
        monkeypatch.setattr(RemoteNode, "ping", lambda self: True)

        result = registry.is_local_agent_ready()

        # Should return True when socket connects successfully
        assert result is True

    def test_is_local_agent_ready_socket_failure(self, monkeypatch):
        """Test when socket connection fails."""
        from llauncher.remote.registry import NodeRegistry
        from unittest.mock import MagicMock

        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()

        # Mock get_node to return None (no local node in registry)
        def mock_get_node(name):
            return None

        monkeypatch.setattr(registry, "get_node", mock_get_node)

        # Mock ping to return False (local node not responding)
        def mock_ping(self):
            return False

        monkeypatch.setattr(RemoteNode, "ping", mock_ping)

        # Mock socket.socket to return a mock socket whose connect raises exception
        def mock_socket_constructor(family, type_, proto=0):
            # Make __enter__ return self so that the with statement works correctly
            mock_socket = MagicMock()
            mock_socket.__enter__.return_value = mock_socket
            def mock_connect(*args, **kwargs):
                raise ConnectionRefusedError("Connection refused")
            mock_socket.connect = mock_connect
            def mock_settimeout(timeout):
                pass
            mock_socket.settimeout = mock_settimeout
            return mock_socket

        # Patch socket.socket at the module level where it's used
        monkeypatch.setattr("socket.socket", mock_socket_constructor)

        result = registry.is_local_agent_ready()

        # Should return False when socket fails
        assert result is False

    def test_is_local_agent_ready_os_error(self, monkeypatch):
        """Test when socket connection raises OSError."""
        from llauncher.remote.registry import NodeRegistry
        from unittest.mock import MagicMock

        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()

        # Mock get_node to return None (no local node in registry)
        def mock_get_node(name):
            return None

        monkeypatch.setattr(registry, "get_node", mock_get_node)

        # Mock ping to return False (local node not responding)
        monkeypatch.setattr(RemoteNode, "ping", lambda self: False)

        # Mock socket.socket to return a mock socket whose connect raises exception
        def mock_socket_constructor(family, type_, proto=0):
            # Make __enter__ return self so that the with statement works correctly
            mock_socket = MagicMock()
            mock_socket.__enter__.return_value = mock_socket
            def mock_connect(*args, **kwargs):
                raise OSError("Network error")
            mock_socket.connect = mock_connect
            def mock_settimeout(timeout):
                pass
            mock_socket.settimeout = mock_settimeout
            return mock_socket

        # Patch socket.socket at the module level where it's used
        monkeypatch.setattr("socket.socket", mock_socket_constructor)

        result = registry.is_local_agent_ready()

        # Should return False when socket raises OSError
        assert result is False


class TestStartLocalAgent:
    """Tests for NodeRegistry.start_local_agent method."""

    def test_start_local_agent_success(self, monkeypatch):
        """Test successful agent start with subprocess."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()

        # Mock subprocess.Popen
        mock_process = MagicMock()
        monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: mock_process)

        # Mock add_node
        added = []

        def mock_add_node(name, host, port, overwrite=False):
            added.append((name, host, port))
            return True, "Added"

        monkeypatch.setattr(registry, "add_node", mock_add_node)

        result = registry.start_local_agent()

        assert result is True

    def test_start_local_agent_failure(self, monkeypatch):
        """Test agent start when subprocess fails."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()

        # Mock subprocess.Popen to raise exception
        monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Failed")))

        result = registry.start_local_agent()

        assert result is False


class TestGetNodeInfoAll:
    """Tests for NodeRegistry.get_node_info_all method."""

    def test_get_node_info_all_mixed(self, monkeypatch):
        """Test when some nodes succeed and some fail."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()
        registry.add_node("online-node", "localhost", 8765)
        registry.add_node("offline-node", "localhost", 8766)

        # Mock node info for online node
        def mock_get_node_info(self):
            if self.name == "online-node":
                return {"node_name": self.name, "status": "online"}
            return None  # Offline node returns None

        monkeypatch.setattr(RemoteNode, "get_node_info", mock_get_node_info)

        result = registry.get_node_info_all()

        # Should only include online node
        assert "online-node" in result
        assert "offline-node" not in result

    def test_get_node_info_all_success(self, monkeypatch):
        """Test when all nodes return info."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()
        registry.add_node("node1", "localhost", 8765)
        registry.add_node("node2", "localhost", 8766)

        # Mock get_node_info to return info for all nodes
        def mock_get_node_info(self):
            return {"node_name": self.name, "status": "online"}

        monkeypatch.setattr(RemoteNode, "get_node_info", mock_get_node_info)

        result = registry.get_node_info_all()

        assert len(result) == 2
        assert "node1" in result
        assert "node2" in result


class TestRefreshAll:
    """Tests for NodeRegistry.refresh_all method."""

    def test_refresh_all_results(self, monkeypatch):
        """Test that refresh_all returns status dictionary."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()
        registry.add_node("node1", "localhost", 8765)
        registry.add_node("node2", "localhost", 8766)

        # Mock ping to return success and set status
        def mock_ping(self):
            self.status = NodeStatus.ONLINE
            return True

        monkeypatch.setattr(RemoteNode, "ping", mock_ping)

        results = registry.refresh_all()

        assert len(results) == 2
        assert "node1" in results
        assert "node2" in results
        assert results["node1"] == NodeStatus.ONLINE
        assert results["node2"] == NodeStatus.ONLINE


class TestToDict:
    """Tests for NodeRegistry.to_dict method."""

    def test_to_dict_conversion(self):
        """Test conversion to dictionary."""
        registry = NodeRegistry()
        registry.add_node("node1", "localhost", 8765)

        data = registry.to_dict()

        assert "node1" in data
        assert data["node1"]["name"] == "node1"
        assert data["node1"]["host"] == "localhost"
        assert data["node1"]["port"] == 8765


class TestGetOnlineNodes:
    """Tests for NodeRegistry.get_online_nodes method."""

    def test_get_online_nodes(self):
        """Test getting online nodes."""
        registry = NodeRegistry()
        registry.add_node("online1", "localhost", 8765)
        registry.add_node("offline1", "localhost", 8766)
        registry.add_node("online2", "localhost", 8767)

        # Set status manually
        registry.get_node("online1").status = NodeStatus.ONLINE
        registry.get_node("offline1").status = NodeStatus.OFFLINE
        registry.get_node("online2").status = NodeStatus.ONLINE

        online_nodes = registry.get_online_nodes()

        assert len(online_nodes) == 2
        assert all(node.status == NodeStatus.ONLINE for node in online_nodes)
        assert "online1" in [node.name for node in online_nodes]
        assert "online2" in [node.name for node in online_nodes]
        assert "offline1" not in [node.name for node in online_nodes]
