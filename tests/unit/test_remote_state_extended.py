"""Extended tests for the RemoteAggregator module."""

import pytest
from unittest.mock import MagicMock, patch

from llauncher.remote.node import RemoteNode, NodeStatus, RemoteServerInfo
from llauncher.remote.registry import NodeRegistry
from llauncher.remote.state import RemoteAggregator


class TestGetAllServersOfflineWithCache:
    """Tests for RemoteAggregator.get_all_servers with offline nodes and cache."""

    def test_get_all_servers_offline_with_cache(self):
        """Test that offline node returns cached data with offline indicator."""
        registry = NodeRegistry()
        registry.add_node("offline-node", "localhost", 8765)

        aggregator = RemoteAggregator(registry)

        # Set up cached servers
        aggregator._server_cache["offline-node"] = [
            RemoteServerInfo(
                node_name="offline-node",
                pid=12345,
                port=8080,
                config_name="test-model",
                start_time="2024-01-01T00:00:00",
                uptime_seconds=3600,
            )
        ]

        # Mock get_status to return None (simulating offline)
        def mock_get_status(self):
            return None

        with patch.object(RemoteNode, "get_status", mock_get_status):
            servers = aggregator.get_all_servers()

            # Should return cached server with offline indicator
            offline_servers = [s for s in servers if s.node_name == "offline-node"]
            assert len(offline_servers) == 1
            assert "[OFFLINE]" in offline_servers[0].config_name

    def test_get_all_servers_no_cache(self):
        """Test get_all_servers when no cached data exists for offline node."""
        registry = NodeRegistry()
        registry.add_node("offline-node", "localhost", 8765)

        aggregator = RemoteAggregator(registry)

        # Mock get_status to return None (simulating offline)
        def mock_get_status(self):
            return None

        with patch.object(RemoteNode, "get_status", mock_get_status):
            servers = aggregator.get_all_servers()

            # Should return empty list for offline node without cache
            offline_servers = [s for s in servers if s.node_name == "offline-node"]
            assert len(offline_servers) == 0


class TestGetAllModelsOfflineWithCache:
    """Tests for RemoteAggregator.get_all_models with offline nodes."""

    def test_get_all_models_offline_with_cache(self):
        """Test that offline node returns cached models with offline flag."""
        registry = NodeRegistry()
        registry.add_node("offline-node", "localhost", 8765)

        aggregator = RemoteAggregator(registry)

        # Cache some models
        aggregator._model_cache["offline-node"] = [
            {"name": "model1", "model_path": "/path/model1"},
        ]

        # Mock get_models to return None (simulating offline)
        def mock_get_models(self):
            return None

        with patch.object(RemoteNode, "get_models", mock_get_models):
            models = aggregator.get_all_models()

            # Should return cached models with offline flag
            offline_models = models.get("offline-node", [])
            assert len(offline_models) == 1
            assert offline_models[0]["_offline"] is True

    def test_get_all_models_success(self):
        """Test get_all_models when nodes are online."""
        registry = NodeRegistry()
        registry.add_node("online-node", "localhost", 8765)

        aggregator = RemoteAggregator(registry)

        # Mock get_models to return models
        def mock_get_models(self):
            return [
                {"name": "model1", "model_path": "/path/model1"},
                {"name": "model2", "model_path": "/path/model2"},
            ]

        with patch.object(RemoteNode, "get_models", mock_get_models):
            models = aggregator.get_all_models()

            assert "online-node" in models
            assert len(models["online-node"]) == 2


class TestGetModelsByName:
    """Tests for RemoteAggregator.get_models_by_name method."""

    def test_get_models_by_name_grouping(self):
        """Test grouping models by name across nodes."""
        registry = NodeRegistry()
        registry.add_node("node1", "localhost", 8765)
        registry.add_node("node2", "localhost", 8766)

        aggregator = RemoteAggregator(registry)

        # Mock get_models to return different models per node
        def mock_get_models(self):
            if self.name == "node1":
                return [
                    {"name": "model1", "model_path": "/path/model1"},
                    {"name": "model2", "model_path": "/path/model2"},
                ]
            else:
                return [
                    {"name": "model1", "model_path": "/path/model1-remote"},
                    {"name": "model3", "model_path": "/path/model3"},
                ]

        with patch.object(RemoteNode, "get_models", mock_get_models):
            models_by_name = aggregator.get_models_by_name()

            # model1 should appear on both nodes
            assert "model1" in models_by_name
            model1_nodes = [node for node, _ in models_by_name["model1"]]
            assert "node1" in model1_nodes
            assert "node2" in model1_nodes

            # model2 only on node1
            assert "model2" in models_by_name
            model2_nodes = [node for node, _ in models_by_name["model2"]]
            assert "node1" in model2_nodes
            assert "node2" not in model2_nodes


class TestStartOnNode:
    """Tests for RemoteAggregator.start_on_node method."""

    def test_start_on_node_not_found(self):
        """Test starting on a nonexistent node returns error."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        result = aggregator.start_on_node("nonexistent", "test-model")

        assert result is not None
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_start_on_node_success(self):
        """Test starting on an existing node."""
        registry = NodeRegistry()
        registry.add_node("test-node", "localhost", 8765)

        aggregator = RemoteAggregator(registry)

        # Mock start_server on node
        def mock_start_server(self, model_name):
            return {"success": True, "port": 8080}

        with patch.object(RemoteNode, "start_server", mock_start_server):
            result = aggregator.start_on_node("test-node", "test-model")

            assert result["success"] is True
            assert result["port"] == 8080


class TestStopOnNode:
    """Tests for RemoteAggregator.stop_on_node method."""

    def test_stop_on_node_not_found(self):
        """Test stopping on a nonexistent node returns error."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        result = aggregator.stop_on_node("nonexistent", 8080)

        assert result is not None
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_stop_on_node_success(self):
        """Test stopping on an existing node."""
        registry = NodeRegistry()
        registry.add_node("test-node", "localhost", 8765)

        aggregator = RemoteAggregator(registry)

        # Mock stop_server on node
        def mock_stop_server(self, port):
            return {"success": True}

        with patch.object(RemoteNode, "stop_server", mock_stop_server):
            result = aggregator.stop_on_node("test-node", 8080)

            assert result["success"] is True


class TestGetLogsOnNode:
    """Tests for RemoteAggregator.get_logs_on_node method."""

    def test_get_logs_on_node_not_found(self):
        """Test getting logs from a nonexistent node returns None."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        result = aggregator.get_logs_on_node("nonexistent", 8080, lines=100)

        assert result is None

    def test_get_logs_on_node_success(self):
        """Test getting logs from an existing node."""
        registry = NodeRegistry()
        registry.add_node("test-node", "localhost", 8765)

        aggregator = RemoteAggregator(registry)

        # Mock get_logs on node
        def mock_get_logs(self, port, lines):
            return ["Log line 1", "Log line 2", "Log line 3"]

        with patch.object(RemoteNode, "get_logs", mock_get_logs):
            result = aggregator.get_logs_on_node("test-node", 8080, lines=100)

            assert result == ["Log line 1", "Log line 2", "Log line 3"]


class TestRefreshAllNodes:
    """Tests for RemoteAggregator.refresh_all_nodes method."""

    def test_refresh_all_nodes_status(self):
        """Test that refresh_all_nodes returns status dictionary."""
        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()
        registry.add_node("node1", "localhost", 8765)
        registry.add_node("node2", "localhost", 8766)

        aggregator = RemoteAggregator(registry)

        # Mock refresh_all on registry
        def mock_refresh_all(*args, **kwargs):
            return {"node1": NodeStatus.ONLINE, "node2": NodeStatus.OFFLINE}

        with patch.object(NodeRegistry, "refresh_all", side_effect=mock_refresh_all):
            status = aggregator.refresh_all_nodes()

            assert "node1" in status
            assert "node2" in status
            assert status["node1"] == "online"
            assert status["node2"] == "offline"


class TestGetSummary:
    """Tests for RemoteAggregator.get_summary method."""

    def test_get_summary_counts(self):
        """Test that get_summary returns correct counts."""
        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()
        registry.add_node("node1", "localhost", 8765)
        registry.add_node("node2", "localhost", 8766)
        registry.add_node("node3", "localhost", 8767)

        aggregator = RemoteAggregator(registry)

        # Mock refresh_all to set specific statuses
        def mock_refresh_all(self):
            # Set the statuses as expected by the test
            self.get_node("node1").status = NodeStatus.ONLINE
            self.get_node("node2").status = NodeStatus.ONLINE
            self.get_node("node3").status = NodeStatus.OFFLINE
            return {
                "node1": NodeStatus.ONLINE,
                "node2": NodeStatus.ONLINE,
                "node3": NodeStatus.OFFLINE
            }

        # Mock caches
        aggregator._server_cache = {
            "node1": [RemoteServerInfo("node1", 1, 8080, "model1", "2024-01-01T00:00:00", 0)],
            "node2": [RemoteServerInfo("node2", 2, 8081, "model2", "2024-01-01T00:00:00", 0)],
        }

        # Mock get_status for online nodes to return server data
        def mock_get_status(self):
            if self.name == "node1":
                return {
                    "running_servers": [
                        {
                            "pid": 1,
                            "port": 8080,
                            "config_name": "model1",
                            "start_time": "2024-01-01T00:00:00",
                            "uptime_seconds": 0,
                        }
                    ]
                }
            elif self.name == "node2":
                return {
                    "running_servers": [
                        {
                            "pid": 2,
                            "port": 8081,
                            "config_name": "model2",
                            "start_time": "2024-01-01T00:00:00",
                            "uptime_seconds": 0,
                        }
                    ]
                }
            return None

        # Mock get_all_models
        def mock_get_all_models(self):
            return {
                "node1": [{"name": "model1"}],
                "node2": [{"name": "model2"}],
                "node3": [{"name": "model3"}],
            }

        with patch.object(NodeRegistry, "refresh_all", mock_refresh_all):
            with patch.object(RemoteNode, "get_status", mock_get_status):
                with patch.object(RemoteAggregator, "get_all_models", mock_get_all_models):
                    summary = aggregator.get_summary()

                    # Check counts
                    assert summary["total_nodes"] == 3
                    assert summary["online_nodes"] == 2
                    assert summary["offline_nodes"] == 1
                    assert summary["total_servers"] == 2
                    assert summary["total_models"] == 3


class TestAggregatorEmptyRegistry:
    """Tests for RemoteAggregator with empty registry."""

    def test_aggregator_empty_registry(self):
        """Test aggregator with no nodes."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        servers = aggregator.get_all_servers()
        models = aggregator.get_all_models()

        assert isinstance(servers, list)
        assert isinstance(models, dict)

    def test_aggregator_with_custom_registry(self):
        """Test aggregator accepts custom registry."""
        custom_registry = NodeRegistry()
        custom_registry.add_node("custom", "localhost", 8765)

        aggregator = RemoteAggregator(custom_registry)

        assert aggregator.registry is custom_registry


class TestGetAllServersEmptyNodes:
    """Tests for RemoteAggregator.get_all_servers when no nodes configured."""

    def test_get_all_servers_empty_nodes(self):
        """Test getting servers when no nodes are configured."""
        registry = NodeRegistry()
        aggregator = RemoteAggregator(registry)

        servers = aggregator.get_all_servers()

        assert isinstance(servers, list)
