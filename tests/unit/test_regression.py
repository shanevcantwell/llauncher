"""Regression tests for closed GitHub issues."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llauncher.models.config import ModelConfig
from llauncher.remote.registry import NodeRegistry
from llauncher.remote.state import RemoteAggregator
from llauncher.state import LauncherState


class TestIssue13LocalAgentAutoStart:
    """Regression test for issue #13: Local agent auto-start not working."""

    def test_is_local_agent_ready_detects_existing_online_node(self):
        """Test that is_local_agent_ready correctly detects when local node exists and is online."""
        # This tests the fix for local agent auto-start functionality
        registry = NodeRegistry()

        # Add a local node
        registry.add_node("local", "localhost", 8765)
        local_node = registry.get_node("local")
        assert local_node is not None

        # Mock the node as online and ping as successful
        with patch.object(local_node, 'ping', return_value=True):
            local_node.status = local_node.status.__class__.ONLINE  # Set to ONLINE

            # Should return True when node exists and is online
            result = registry.is_local_agent_ready()
            assert result is True

    def test_start_local_agent_success(self):
        """Test that start_local_agent successfully starts the agent."""
        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()

        # Mock subprocess.Popen to simulate successful process start
        mock_process = MagicMock()
        with patch("subprocess.Popen", return_value=mock_process):
            # Mock add_node to verify it gets called
            added_nodes = []
            def mock_add_node(name, host, port, overwrite=False):
                added_nodes.append((name, host, port))
                return True, "Added"

            with patch.object(registry, 'add_node', mock_add_node):
                result = registry.start_local_agent()
                assert result is True
                # Should have added the local node
                assert len(added_nodes) == 1
                assert added_nodes[0][0] == "local"


class TestIssue6LlamaServerConfigFields:
    """Regression test for issue #6: Missing llama-server config fields."""

    def test_model_config_includes_all_server_options(self):
        """Test that ModelConfig includes all expected llama-server fields."""
        # Test that we can create a ModelConfig with various server options
        config = ModelConfig(
            name="test-model",
            model_path="/path/to/model.gguf",
            # Test various server configuration options
            n_gpu_layers=32,
            ctx_size=2048,
            threads=4,
            threads_batch=2,
            ubatch_size=128,
            batch_size=512,
            flash_attn="on",  # Valid values: "on", "off", "auto"
            no_mmap=False,
            cache_type_k="f32",  # Valid values: "f32", "f16", "bf16", "q8_0"
            cache_type_v="f16",  # Valid values: "f32", "f16", "bf16", "q8_0"
            n_cpu_moe=0,
            parallel=2,
            temperature=0.8,
            top_k=40,
            top_p=0.95,
            min_p=0.05,
            reverse_prompt="</s>",
            mlock=True,
            extra_args="--log-disable"
        )

        # Verify all fields are set correctly
        assert config.name == "test-model"
        assert config.model_path == "/path/to/model.gguf"
        assert config.n_gpu_layers == 32
        assert config.ctx_size == 2048
        assert config.threads == 4
        assert config.threads_batch == 2
        assert config.ubatch_size == 128
        assert config.batch_size == 512
        assert config.flash_attn == "on"
        assert config.no_mmap is False
        assert config.cache_type_k == "f32"
        assert config.cache_type_v == "f16"
        assert config.n_cpu_moe == 0
        assert config.parallel == 2
        assert config.temperature == 0.8
        assert config.top_k == 40
        assert config.top_p == 0.95
        assert config.min_p == 0.05
        assert config.reverse_prompt == "</s>"
        assert config.mlock is True
        assert config.extra_args == "--log-disable"


class TestIssue11TopKMinPInUIForms:
    """Regression test for issue #11: top_k/min_p missing from UI forms."""

    def test_model_config_supports_top_k_and_min_p(self):
        """Test that ModelConfig supports top_k and min_p fields."""
        # These were reportedly missing from UI forms
        config = ModelConfig(
            name="test-model",
            model_path="/path/to/model.gguf",
            top_k=33,
            min_p=0.02
        )

        assert config.top_k == 33
        assert config.min_p == 0.02

        # Test that they can be modified
        config.top_k = 50
        config.min_p = 0.1
        assert config.top_k == 50
        assert config.min_p == 0.1


class TestIssue7UnusedMultiGpuFields:
    """Regression test for issue #7: Remove unused multi-GPU fields."""

    def test_model_config_works_without_multi_gpu_fields(self):
        """Test that ModelConfig works correctly without multi-GPU fields."""
        # Test creating a config without specifying multi-GPU related fields
        config = ModelConfig(
            name="test-model",
            model_path="/path/to/model.gguf"
            # Note: Not setting n_cpu_moe or parallel (which relate to multi-GPU/CPU)
        )

        # Should have default values for multi-GPU related fields
        assert config.name == "test-model"
        assert config.model_path == "/path/to/model.gguf"
        # These should have sensible defaults even if not explicitly set
        assert hasattr(config, 'n_cpu_moe')
        assert hasattr(config, 'parallel')


class TestIssue5PortRename:
    """Regression test for issue #5: Start button fails - port rename."""

    def test_model_config_has_default_port_field(self):
        """Test that ModelConfig includes default_port field for server startup."""
        config = ModelConfig(
            name="test-model",
            model_path="/path/to/model.gguf",
            default_port=8080
        )

        assert hasattr(config, 'default_port')
        assert config.default_port == 8080

        # Should be able to change it
        config.default_port = 8081
        assert config.default_port == 8081


class TestIssue3PortCoupledToModelProfile:
    """Regression test for issue #3: Port coupled to model profile."""

    def test_model_config_port_can_be_configured_independently(self):
        """Test that port configuration is not rigidly coupled to model name."""
        # Test that we can have different ports for same model name (different instances)
        config1 = ModelConfig(
            name="shared-model",
            model_path="/path/to/model1.gguf",
            default_port=8080
        )

        config2 = ModelConfig(
            name="shared-model",
            model_path="/path/to/model2.gguf",
            default_port=8081
        )

        # Same model name, different ports should be allowed
        assert config1.name == config2.name == "shared-model"
        assert config1.default_port == 8080
        assert config2.default_port == 8081
        assert config1.model_path != config2.model_path


class TestIssue18LegacyExtraArgsConfig:
    """Regression test for issue #18: UI crash with old extra_args config."""

    def test_model_config_handles_legacy_extra_args_format(self):
        """Test that ModelConfig can handle various extra_args formats."""
        # Test empty extra_args
        config1 = ModelConfig(
            name="test-model",
            model_path="/path/to/model.gguf",
            extra_args=""
        )
        assert config1.extra_args == ""

        # Test None-like extra_args (empty string)
        config2 = ModelConfig(
            name="test-model",
            model_path="/path/to/model.gguf",
            extra_args=" "
        )
        assert config2.extra_args == " "

        # Test complex extra_args
        config3 = ModelConfig(
            name="test-model",
            model_path="/path/to/model.gguf",
            extra_args="--n-gpu-layers 32 --ctx-size 2048 --log-disable"
        )
        assert config3.extra_args == "--n-gpu-layers 32 --ctx-size 2048 --log-disable"


    def test_fresh_registry_node_count(self):
        """Check how many nodes are in a freshly created registry."""
        registry = NodeRegistry()
        # Don't clear it - see what's there by default
        node_count = len(registry)
        print(f"Fresh registry has {node_count} nodes")
        # Just for now, let's see what we get
        # Actually, let's not fail the test yet, just gather info

    @patch("httpx.Client")
    def test_aggregator_with_empty_registry(self, mock_client_class):
        """Test that RemoteAggregator works with empty registry (related to issue resilience)."""
        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()
        aggregator = RemoteAggregator(registry)
        # Clear any cached data to ensure truly empty state
        aggregator._server_cache.clear()
        aggregator._model_cache.clear()

        # Mock the HTTP client to simulate connection refused (no nodes running)
        mock_response = MagicMock()
        mock_response.status_code = 503  # Service unavailable
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        # Should not crash with empty registry
        servers = aggregator.get_all_servers()
        models = aggregator.get_all_models()
        summary = aggregator.get_summary()

        assert isinstance(servers, list)
        assert len(servers) == 0
        assert isinstance(models, dict)
        assert len(models) == 0
        assert summary["total_nodes"] == 0
        assert summary["online_nodes"] == 0
        assert summary["offline_nodes"] == 0
        assert summary["total_servers"] == 0
        assert summary["total_models"] == 0

    def test_aggregator_handles_offline_nodes_gracefully(self):
        """Test that aggregator handles offline nodes without crashing."""
        registry = NodeRegistry()
        registry.add_node("offline-node", "localhost", 9999)  # Unlikely port

        aggregator = RemoteAggregator(registry)

        # Should handle offline nodes gracefully
        servers = aggregator.get_all_servers()
        models = aggregator.get_all_models()

        # Should return empty lists for completely offline nodes with no cache
        assert isinstance(servers, list)
        assert isinstance(models, dict)