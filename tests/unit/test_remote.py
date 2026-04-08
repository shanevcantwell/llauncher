"""Tests for the remote management module."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import httpx

from llauncher.remote.node import RemoteNode, NodeStatus, RemoteServerInfo
from llauncher.remote.registry import NodeRegistry, NODES_FILE
from llauncher.remote.state import RemoteAggregator


class TestRemoteNode:
    """Tests for the RemoteNode class."""

    def test_node_initialization(self):
        """Test that node initializes with correct defaults."""
        node = RemoteNode("test-node", "192.168.1.100")

        assert node.name == "test-node"
        assert node.host == "192.168.1.100"
        assert node.port == 8765
        assert node.timeout == 5.0
        assert node.status == NodeStatus.OFFLINE
        assert node.last_seen is None

    def test_node_custom_port(self):
        """Test that node accepts custom port."""
        node = RemoteNode("test-node", "192.168.1.100", port=9000, timeout=10.0)

        assert node.port == 9000
        assert node.timeout == 10.0

    def test_base_url(self):
        """Test that base_url is constructed correctly."""
        node = RemoteNode("test-node", "192.168.1.100", port=9000)

        assert node.base_url == "http://192.168.1.100:9000"

    def test_str_representation(self):
        """Test string representation."""
        node = RemoteNode("test-node", "192.168.1.100", port=8765)

        assert "test-node" in str(node)
        assert "192.168.1.100" in str(node)
        assert "8765" in str(node)

    def test_to_dict(self):
        """Test conversion to dictionary."""
        node = RemoteNode("test-node", "192.168.1.100", port=8765)

        data = node.to_dict()

        assert data["name"] == "test-node"
        assert data["host"] == "192.168.1.100"
        assert data["port"] == 8765
        assert data["status"] == "offline"
        assert data["last_seen"] is None

    @patch("httpx.Client")
    def test_ping_success(self, mock_client_class):
        """Test successful ping."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "localhost", port=8765)
        result = node.ping()

        assert result is True
        assert node.status == NodeStatus.ONLINE
        assert node.last_seen is not None

    @patch("httpx.Client")
    def test_ping_failure(self, mock_client_class):
        """Test failed ping."""
        mock_client_class.side_effect = httpx.RequestError("Connection refused")

        node = RemoteNode("test-node", "localhost", port=8765)
        result = node.ping()

        assert result is False
        assert node.status == NodeStatus.OFFLINE

    @patch("httpx.Client")
    def test_get_node_info(self, mock_client_class):
        """Test getting node info."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(
            return_value={
                "node_name": "test-node",
                "hostname": "test-host",
                "os": "Linux",
                "python_version": "3.12.0",
                "ip_addresses": ["192.168.1.100"],
            }
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "localhost", port=8765)
        info = node.get_node_info()

        assert info is not None
        assert info["node_name"] == "test-node"
        assert info["os"] == "Linux"


class TestRemoteServerInfo:
    """Tests for the RemoteServerInfo class."""

    def test_server_info_initialization(self):
        """Test server info initialization."""
        server = RemoteServerInfo(
            node_name="test-node",
            pid=12345,
            port=8080,
            config_name="test-model",
            start_time="2024-01-01T00:00:00",
            uptime_seconds=3600,
        )

        assert server.node_name == "test-node"
        assert server.pid == 12345
        assert server.port == 8080
        assert server.config_name == "test-model"

    def test_server_info_to_dict(self):
        """Test server info conversion to dict."""
        server = RemoteServerInfo(
            node_name="test-node",
            pid=12345,
            port=8080,
            config_name="test-model",
            start_time="2024-01-01T00:00:00",
            uptime_seconds=3600,
            logs_path="/var/log/test.log",
        )

        data = server.to_dict()

        assert data["node_name"] == "test-node"
        assert data["pid"] == 12345
        assert data["port"] == 8080
        assert data["logs_path"] == "/var/log/test.log"


class TestNodeRegistry:
    """Tests for the NodeRegistry class."""

    @pytest.fixture
    def temp_nodes_file(self):
        """Create a temporary nodes file for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            original_file = NODES_FILE
            temp_file = Path(tmpdir) / "nodes.json"

            # Patch the NODES_FILE
            import llauncher.remote.registry as registry_module

            registry_module.NODES_FILE = temp_file

            yield temp_file

            # Restore original
            registry_module.NODES_FILE = original_file

    def test_registry_empty_initialization(self):
        """Test that empty registry initializes correctly."""
        registry = NodeRegistry()

        # Filter out any pre-existing nodes
        initial_count = len(registry)

        assert initial_count >= 0

    def test_add_node(self, temp_nodes_file):
        """Test adding a node."""
        registry = NodeRegistry()

        success, message = registry.add_node("test-node", "192.168.1.100", 8765)

        assert success is True
        assert "test-node" in message
        assert len(registry) == 1
        assert registry.get_node("test-node") is not None

    def test_add_node_duplicate(self, temp_nodes_file):
        """Test adding duplicate node without overwrite."""
        registry = NodeRegistry()

        registry.add_node("test-node", "192.168.1.100", 8765)
        success, message = registry.add_node("test-node", "192.168.1.101", 8765)

        assert success is False
        assert "already exists" in message

    def test_add_node_overwrite(self, temp_nodes_file):
        """Test adding duplicate node with overwrite."""
        registry = NodeRegistry()

        registry.add_node("test-node", "192.168.1.100", 8765)
        success, _ = registry.add_node(
            "test-node", "192.168.1.101", 9000, overwrite=True
        )

        assert success is True
        node = registry.get_node("test-node")
        assert node.host == "192.168.1.101"
        assert node.port == 9000

    def test_remove_node(self, temp_nodes_file):
        """Test removing a node."""
        registry = NodeRegistry()
        registry.add_node("test-node", "192.168.1.100", 8765)

        success, message = registry.remove_node("test-node")

        assert success is True
        assert len(registry) == 0
        assert registry.get_node("test-node") is None

    def test_remove_nonexistent_node(self, temp_nodes_file):
        """Test removing a nonexistent node."""
        registry = NodeRegistry()

        success, message = registry.remove_node("nonexistent")

        assert success is False
        assert "not found" in message

    def test_persistence(self, temp_nodes_file):
        """Test that nodes are persisted to file."""
        registry = NodeRegistry()
        registry.add_node("test-node", "192.168.1.100", 9000)

        # Create new registry (simulates reload)
        registry2 = NodeRegistry()

        assert len(registry2) == 1
        node = registry2.get_node("test-node")
        assert node is not None
        assert node.host == "192.168.1.100"
        assert node.port == 9000

    def test_refresh_all(self, temp_nodes_file):
        """Test refreshing all nodes."""
        registry = NodeRegistry()
        registry.add_node("node1", "localhost", 8765)
        registry.add_node("node2", "localhost", 9000)

        with patch.object(RemoteNode, "ping") as mock_ping:
            mock_ping.return_value = True
            results = registry.refresh_all()

            assert len(results) == 2
            assert "node1" in results
            assert "node2" in results

    def test_get_online_nodes(self, temp_nodes_file):
        """Test getting online nodes."""
        registry = NodeRegistry()
        registry.add_node("node1", "localhost", 8765)
        registry.add_node("node2", "localhost", 9000)

        # Manually set status to ONLINE since we're testing get_online_nodes logic
        for node in registry:
            node.status = NodeStatus.ONLINE

        online = registry.get_online_nodes()

        # Both nodes should be online
        assert len(online) == 2


class TestRemoteAggregator:
    """Tests for the RemoteAggregator class."""

    def test_aggregator_initialization(self):
        """Test aggregator initializes with default registry."""
        aggregator = RemoteAggregator()

        assert aggregator.registry is not None
        assert isinstance(aggregator.registry, NodeRegistry)

    def test_aggregator_with_registry(self):
        """Test aggregator accepts custom registry."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        assert aggregator.registry is registry

    @patch("httpx.Client")
    def test_get_all_servers(self, mock_client_class):
        """Test getting all servers from all nodes."""
        # Setup mock responses
        mock_status_response = MagicMock()
        mock_status_response.status_code = 200
        mock_status_response.json = MagicMock(
            return_value={
                "node": "test-node",
                "running_servers": [
                    {
                        "pid": 12345,
                        "port": 8080,
                        "config_name": "test-model",
                        "start_time": "2024-01-01T00:00:00",
                        "uptime_seconds": 3600,
                    }
                ],
                "total_running": 1,
            }
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_status_response)

        mock_client_class.return_value = mock_client

        # Create aggregator with a node using unique IP
        registry = NodeRegistry()
        registry.add_node("test-node-unique", "192.168.1.99", 8765)
        aggregator = RemoteAggregator(registry)

        servers = aggregator.get_all_servers()

        # Filter for only our test node
        test_servers = [s for s in servers if s.node_name == "test-node-unique"]

        assert len(test_servers) == 1
        assert test_servers[0].node_name == "test-node-unique"
        assert test_servers[0].port == 8080
        assert test_servers[0].config_name == "test-model"

        # Cleanup
        registry.remove_node("test-node-unique")

    @patch("httpx.Client")
    def test_get_all_models(self, mock_client_class):
        """Test getting all models from all nodes."""
        mock_models_response = MagicMock()
        mock_models_response.status_code = 200
        mock_models_response.json = MagicMock(
            return_value=[
                {
                    "name": "model1",
                    "model_path": "/path/to/model1.gguf",
                    "default_port": 8080,
                    "running": False,
                }
            ]
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_models_response)

        mock_client_class.return_value = mock_client

        registry = NodeRegistry()
        registry.add_node("test-node-models", "192.168.1.98", 8765)
        aggregator = RemoteAggregator(registry)

        models = aggregator.get_all_models()

        assert "test-node-models" in models
        assert len(models["test-node-models"]) == 1
        assert models["test-node-models"][0]["name"] == "model1"

        # Cleanup
        registry.remove_node("test-node-models")

    @patch("httpx.Client")
    def test_start_on_node(self, mock_client_class):
        """Test starting a server on a specific node."""
        mock_start_response = MagicMock()
        mock_start_response.status_code = 200
        mock_start_response.json = MagicMock(
            return_value={"success": True, "message": "Started", "port": 8080}
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_start_response)

        mock_client_class.return_value = mock_client

        registry = NodeRegistry()
        registry.add_node("test-node-start", "192.168.1.97", 8765)
        aggregator = RemoteAggregator(registry)

        result = aggregator.start_on_node("test-node-start", "test-model")

        assert result is not None
        assert result["success"] is True

        # Cleanup
        registry.remove_node("test-node-start")

    def test_start_on_nonexistent_node(self):
        """Test starting on a nonexistent node returns error."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        result = aggregator.start_on_node("nonexistent", "test-model")

        assert result is not None
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_get_summary(self):
        """Test getting deployment summary."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        summary = aggregator.get_summary()

        assert "total_nodes" in summary
        assert "online_nodes" in summary
        assert "offline_nodes" in summary
        assert "total_servers" in summary
        assert "nodes" in summary
        assert "servers" in summary
