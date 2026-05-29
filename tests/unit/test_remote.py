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

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        result = node.ping()

        assert result is True
        assert node.status == NodeStatus.ONLINE
        assert node.last_seen is not None

    @patch("httpx.Client")
    def test_ping_failure(self, mock_client_class):
        """Test failed ping."""
        mock_client_class.side_effect = httpx.RequestError("Connection refused")

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
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

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        info = node.get_node_info()

        assert info is not None
        assert info["node_name"] == "test-node"
        assert info["os"] == "Linux"

    @patch("httpx.Client")
    def test_ping_error_status(self, mock_client_class):
        """Test ping with non-200 status code sets ERROR status."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        result = node.ping()

        assert result is False
        assert node.status == NodeStatus.ERROR
        assert "Unexpected status" in node._error_message

    @patch("httpx.Client")
    def test_get_node_info_failure(self, mock_client_class):
        """Test get_node_info returns None on failure."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        info = node.get_node_info()

        assert info is None
        assert node.status == NodeStatus.OFFLINE

    @patch("httpx.Client")
    def test_get_status_success(self, mock_client_class):
        """Test get_status returns running servers."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(
            return_value={
                "node": "test-node",
                "running_servers": [{"pid": 12345, "port": 8080, "config_name": "model1"}],
                "total_running": 1,
            }
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        status = node.get_status()

        assert status is not None
        assert status["total_running"] == 1
        assert node.status == NodeStatus.ONLINE

    @patch("httpx.Client")
    def test_get_models_success(self, mock_client_class):
        """Test get_models returns model list."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(
            return_value=[
                {"name": "model1", "model_path": "/path/model.gguf"},
                {"name": "model2", "model_path": "/path/model2.gguf"},
            ]
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        models = node.get_models()

        assert models is not None
        assert len(models) == 2
        assert node.status == NodeStatus.ONLINE

    @patch("httpx.Client")
    def test_start_server_success(self, mock_client_class):
        """Test start_server returns success result."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(
            return_value={"success": True, "message": "Started", "port": 8080}
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        result = node.start_server("test-model", 8081)

        assert result is not None
        assert result["success"] is True
        assert result["port"] == 8080
        # Path is port-keyed; body carries the model.
        post_kwargs = mock_client.post.call_args
        assert post_kwargs.args[0].endswith("/start/8081")
        assert post_kwargs.kwargs["json"] == {"model": "test-model"}

    @patch("httpx.Client")
    def test_start_server_error_response(self, mock_client_class):
        """A non-200 response surfaces as ``success=False`` with the agent's
        structured detail propagated."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json = MagicMock(
            return_value={
                "detail": {
                    "success": False,
                    "action": "error",
                    "port": 8081,
                    "model": "ghost",
                    "message": "Model not found: ghost",
                }
            }
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        result = node.start_server("ghost", 8081)

        assert result is not None
        assert result["success"] is False
        assert result["action"] == "error"

    @patch("httpx.Client")
    def test_start_server_conflict(self, mock_client_class):
        """409 → ``success=False`` with the conflict detail propagated."""
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.json = MagicMock(
            return_value={
                "detail": {
                    "success": False,
                    "action": "rejected_occupied",
                    "port": 8081,
                    "model": "other-model",
                    "message": "Port 8081 is occupied by other-model",
                }
            }
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        result = node.start_server("test-model", 8081)

        assert result is not None
        assert result["success"] is False
        assert result["action"] == "rejected_occupied"

    @patch("httpx.Client")
    def test_stop_server_success(self, mock_client_class):
        """Test stop_server returns success result."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(
            return_value={"success": True, "message": "Stopped"}
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        result = node.stop_server(8080)

        assert result is not None
        assert result["success"] is True

    @patch("httpx.Client")
    def test_stop_server_not_found(self, mock_client_class):
        """Test stop_server returns 404 error for missing server."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        result = node.stop_server(9999)

        assert result is not None
        assert result["success"] is False
        assert "port 9999" in result["error"]

    @patch("httpx.Client")
    def test_get_logs_success(self, mock_client_class):
        """Test get_logs returns log lines."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(
            return_value={
                "port": 8080,
                "lines": ["Line 1", "Line 2", "Line 3"],
            }
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        logs = node.get_logs(8080, lines=100)

        assert logs is not None
        assert logs == ["Line 1", "Line 2", "Line 3"]

    @patch("httpx.Client")
    def test_get_logs_failure(self, mock_client_class):
        """Test get_logs returns None on failure."""
        mock_client_class.side_effect = httpx.RequestError("Connection failed")

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        logs = node.get_logs(8080)

        assert logs is None
        assert node.status == NodeStatus.OFFLINE


class TestRemoteNodeReadAudit:
    """Issue #64: ``RemoteNode.read_audit`` HTTP + self-loop branches."""

    @patch("httpx.Client")
    def test_read_audit_success_returns_list(self, mock_client_class):
        """HTTP 200 with a JSON list payload is returned verbatim."""
        payload = [
            {
                "timestamp": "2026-05-09T00:00:00+00:00",
                "action": "started",
                "result": "success",
                "caller": "agent",
                "port": 8080,
                "model": "m",
                "from_model": None,
                "pid": 1,
                "message": "",
            }
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=payload)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        result = node.read_audit(limit=50, action_filter="started")

        assert result == payload
        assert node.status == NodeStatus.ONLINE
        # Confirm query params + URL routing.
        called_kwargs = mock_client.get.call_args.kwargs
        assert called_kwargs["params"]["limit"] == 50
        assert called_kwargs["params"]["action"] == "started"
        assert mock_client.get.call_args.args[0].endswith("/audit")

    @patch("httpx.Client")
    def test_read_audit_non_200_returns_none(self, mock_client_class):
        """Non-200 responses surface as ``None``."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        assert node.read_audit() is None

    @patch("httpx.Client")
    def test_read_audit_request_error_marks_offline(self, mock_client_class):
        """A transport-level error marks the node offline and returns ``None``."""
        mock_client_class.side_effect = httpx.RequestError("Connection failed")

        node = RemoteNode("test-node", "192.168.1.100", port=8765)
        assert node.read_audit() is None
        assert node.status == NodeStatus.OFFLINE

    def test_read_audit_self_loop_reads_in_process(self, tmp_path, monkeypatch):
        """Self-loop branch bypasses HTTP and reads the on-disk JSONL."""
        from llauncher.core import audit_log

        # Redirect audit-log writes to a tmp file.
        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(
            "llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path
        )

        audit_log.record(
            audit_log.AuditAction.STARTED,
            audit_log.AuditResult.SUCCESS,
            caller="test",
            port=8080,
            model="m",
            message="started",
        )

        # ``name == "local"`` triggers the self-loop short-circuit.
        node = RemoteNode("local", "127.0.0.1", port=8765)

        with patch("httpx.Client") as mock_client_class:
            entries = node.read_audit(limit=10)
            # HTTP must NOT have been used on the self-loop path.
            mock_client_class.assert_not_called()

        assert isinstance(entries, list)
        assert len(entries) == 1
        assert entries[0]["action"] == "started"
        assert entries[0]["result"] == "success"
        assert node.status == NodeStatus.ONLINE

    def test_read_audit_self_loop_applies_filters(self, tmp_path, monkeypatch):
        """In-process branch honors ``action_filter`` / ``result_filter``."""
        from llauncher.core import audit_log

        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr(
            "llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path
        )

        audit_log.record(
            audit_log.AuditAction.STARTED,
            audit_log.AuditResult.SUCCESS,
            caller="t",
        )
        audit_log.record(
            audit_log.AuditAction.STOPPED,
            audit_log.AuditResult.SUCCESS,
            caller="t",
        )

        node = RemoteNode("local", "127.0.0.1", port=8765)
        entries = node.read_audit(action_filter="stopped")
        assert len(entries) == 1
        assert entries[0]["action"] == "stopped"


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

    def test_aggregator_preserves_explicitly_passed_empty_registry(self):
        """Issue #45 regression: ``NodeRegistry`` defines ``__len__``, so an
        empty registry is falsy. A previous ``registry or NodeRegistry()``
        guard would silently swap an explicitly-passed empty registry for a
        fresh one. The current guard uses ``is not None`` and must preserve
        the caller's instance even when it is empty.
        """
        empty = NodeRegistry()
        # Ensure we're exercising the falsy-but-not-None case.
        empty._nodes.clear()
        assert len(empty) == 0
        assert not empty  # falsy by __len__
        agg = RemoteAggregator(empty)
        assert agg.registry is empty

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

        result = aggregator.start_on_node("test-node-start", "test-model", 8081)

        assert result is not None
        assert result["success"] is True

        # Cleanup
        registry.remove_node("test-node-start")

    def test_start_on_nonexistent_node(self):
        """Test starting on a nonexistent node returns error."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        result = aggregator.start_on_node("nonexistent", "test-model", 8081)

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

    @patch("httpx.Client")
    def test_stop_on_node_success(self, mock_client_class):
        """Test stopping a server on a specific node."""
        mock_stop_response = MagicMock()
        mock_stop_response.status_code = 200
        mock_stop_response.json = MagicMock(
            return_value={"success": True, "message": "Stopped"}
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_stop_response)

        mock_client_class.return_value = mock_client

        registry = NodeRegistry()
        registry.add_node("test-node-stop", "192.168.1.96", 8765)
        aggregator = RemoteAggregator(registry)

        result = aggregator.stop_on_node("test-node-stop", 8080)

        assert result is not None
        assert result["success"] is True

        # Cleanup
        registry.remove_node("test-node-stop")

    def test_stop_on_nonexistent_node(self):
        """Test stopping on a nonexistent node returns error."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        result = aggregator.stop_on_node("nonexistent", 8080)

        assert result is not None
        assert result["success"] is False
        assert "not found" in result["error"]

    @patch("httpx.Client")
    def test_get_logs_on_node_success(self, mock_client_class):
        """Test getting logs for a server on a specific node."""
        mock_logs_response = MagicMock()
        mock_logs_response.status_code = 200
        mock_logs_response.json = MagicMock(
            return_value={"port": 8080, "lines": ["Log line 1", "Log line 2"]}
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_logs_response)

        mock_client_class.return_value = mock_client

        registry = NodeRegistry()
        registry.add_node("test-node-logs", "192.168.1.95", 8765)
        aggregator = RemoteAggregator(registry)

        logs = aggregator.get_logs_on_node("test-node-logs", 8080, lines=100)

        assert logs is not None
        assert logs == ["Log line 1", "Log line 2"]

        # Cleanup
        registry.remove_node("test-node-logs")

    def test_get_logs_on_nonexistent_node(self):
        """Test getting logs from a nonexistent node returns None."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        logs = aggregator.get_logs_on_node("nonexistent", 8080)

        assert logs is None

    @patch("httpx.Client")
    def test_get_all_servers_empty_nodes(self, mock_client_class):
        """Test getting servers when no nodes are configured."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        servers = aggregator.get_all_servers()

        assert servers == []

    @patch("httpx.Client")
    def test_get_all_models_empty_nodes(self, mock_client_class):
        """Test getting models when no nodes are configured."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        models = aggregator.get_all_models()

        assert models == {}

    @patch("httpx.Client")
    def test_get_all_servers_node_offline(self, mock_client_class):
        """Test getting servers when node is offline."""
        mock_client_class.side_effect = httpx.RequestError("Connection refused")

        registry = NodeRegistry()
        registry.add_node("offline-node", "192.168.1.94", 8765)
        aggregator = RemoteAggregator(registry)

        servers = aggregator.get_all_servers()

        # Should return empty list for offline node
        offline_servers = [s for s in servers if s.node_name == "offline-node"]
        assert len(offline_servers) == 0

        # Cleanup
        registry.remove_node("offline-node")

    @patch("httpx.Client")
    def test_get_all_models_node_offline(self, mock_client_class):
        """Test getting models when node is offline."""
        mock_client_class.side_effect = httpx.RequestError("Connection refused")

        registry = NodeRegistry()
        registry.add_node("offline-node-models", "192.168.1.93", 8765)
        aggregator = RemoteAggregator(registry)

        models = aggregator.get_all_models()

        # Should return empty list for offline node
        offline_models = models.get("offline-node-models", [])
        assert offline_models == []

        # Cleanup
        registry.remove_node("offline-node-models")


# ---------------------------------------------------------------------------
# Issue #62 — RemoteNode self-loop short-circuit per ADR-009
#
# When a RemoteNode resolves to the local agent, verb methods should
# bypass HTTP entirely and call the in-process operations layer. Auth
# is enforced only at the network boundary; the in-process path
# intentionally skips it.
# ---------------------------------------------------------------------------


class TestSelfLoopDetection:
    """``_is_self_loop`` covers two independent signals."""

    def test_name_local_is_self_loop_regardless_of_host(self):
        node = RemoteNode("local", "192.168.1.100", port=9000)
        assert node._is_self_loop() is True

    def test_localhost_host_and_default_port_is_self_loop(self):
        node = RemoteNode("anything", "localhost", port=8765)
        assert node._is_self_loop() is True

    def test_127_0_0_1_with_default_port_is_self_loop(self):
        node = RemoteNode("anything", "127.0.0.1", port=8765)
        assert node._is_self_loop() is True

    def test_localhost_with_different_port_is_NOT_self_loop(self):
        # Same host, different port — different process. Not local.
        node = RemoteNode("anything", "localhost", port=9999)
        assert node._is_self_loop() is False

    def test_remote_host_with_default_port_is_NOT_self_loop(self):
        node = RemoteNode("peer", "192.168.1.100", port=8765)
        assert node._is_self_loop() is False

    def test_hostname_match_with_default_port_is_self_loop(self, monkeypatch):
        import socket
        monkeypatch.setattr(socket, "gethostname", lambda: "my-workstation")
        node = RemoteNode("renamed", "my-workstation", port=8765)
        assert node._is_self_loop() is True


class TestSelfLoopShortCircuit:
    """Verb methods bypass HTTP when self-loop is detected."""

    def test_ping_self_loop_skips_http_and_returns_true(self):
        node = RemoteNode("local", "localhost", port=8765)
        with patch("httpx.Client") as mock_client_class:
            assert node.ping() is True
            mock_client_class.assert_not_called()
        assert node.status == NodeStatus.ONLINE

    def test_start_server_self_loop_calls_ops_directly(self):
        node = RemoteNode("local", "localhost", port=8765)

        canned = MagicMock()
        canned.to_dict.return_value = {
            "success": True,
            "action": "started",
            "port": 8081,
            "model": "qwen",
            "pid": 12345,
            "message": "",
        }
        with patch("httpx.Client") as mock_client_class, \
             patch("llauncher.operations.start", return_value=canned) as mock_start:
            result = node.start_server("qwen", 8081)

        mock_client_class.assert_not_called()
        mock_start.assert_called_once_with("qwen", 8081, caller="local")
        assert result["success"] is True
        assert result["action"] == "started"

    def test_stop_server_self_loop_calls_ops_directly(self):
        node = RemoteNode("local", "localhost", port=8765)
        canned = MagicMock()
        canned.to_dict.return_value = {"success": True, "action": "stopped"}
        with patch("httpx.Client") as mock_client_class, \
             patch("llauncher.operations.stop", return_value=canned) as mock_stop:
            result = node.stop_server(8081)
        mock_client_class.assert_not_called()
        mock_stop.assert_called_once_with(8081, caller="local")
        assert result["success"] is True

    def test_swap_server_self_loop_calls_ops_directly(self):
        node = RemoteNode("local", "localhost", port=8765)
        canned = MagicMock()
        canned.to_dict.return_value = {"success": True, "action": "swapped"}
        with patch("httpx.Client") as mock_client_class, \
             patch("llauncher.operations.swap", return_value=canned) as mock_swap:
            result = node.swap_server("new", 8081)
        mock_client_class.assert_not_called()
        mock_swap.assert_called_once_with("new", 8081, caller="local")
        assert result["success"] is True

    def test_delete_model_self_loop_calls_ops_directly(self):
        node = RemoteNode("local", "localhost", port=8765)
        canned = MagicMock()
        canned.to_dict.return_value = {"success": True, "action": "deleted"}
        with patch("httpx.Client") as mock_client_class, \
             patch("llauncher.operations.delete_model", return_value=canned) as mock_del:
            result = node.delete_model("qwen")
        mock_client_class.assert_not_called()
        mock_del.assert_called_once_with("qwen", caller="local")
        assert result["success"] is True

    def test_self_loop_skips_auth_header_check(self):
        """In-process path is not subject to LLAUNCHER_AGENT_TOKEN — auth is a
        network-boundary concern only."""
        node = RemoteNode("local", "localhost", port=8765, api_key=None)
        canned = MagicMock()
        canned.to_dict.return_value = {"success": True}
        with patch("llauncher.operations.start", return_value=canned):
            # Should not raise even though api_key is None and the agent's
            # token might be set; auth middleware is bypassed.
            result = node.start_server("qwen", 8081)
        assert result["success"] is True

    def test_remote_node_still_uses_http(self):
        """Regression guard: a genuinely remote node still goes over HTTP."""
        node = RemoteNode("peer", "192.168.1.100", port=8765)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "action": "started"}
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.__exit__.return_value = False
        mock_client.post.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client) as mock_client_class, \
             patch("llauncher.operations.start") as mock_ops_start:
            result = node.start_server("qwen", 8081)

        mock_client_class.assert_called_once()
        mock_ops_start.assert_not_called()
        assert result["success"] is True
