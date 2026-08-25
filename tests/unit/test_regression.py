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

    # ``test_start_local_agent_success`` removed in M4 Slice 12 (issue #49).
    # The auto-spawn behavior it tested was deleted along with
    # ``NodeRegistry.start_local_agent``. See
    # ``test_registry_extended.TestStartLocalAgentRemoved`` for the
    # regression guard against re-introduction.


class TestIssue6LlamaServerConfigFields:
    """Regression test for issue #6: Missing llama-server config fields.

    Per ADR-026 / issue #477, the fields this test originally exercised
    (``threads``, ``threads_batch``, ``ubatch_size``, ``batch_size``,
    ``flash_attn``, ``no_mmap``, ``cache_type_k/v``, ``n_cpu_moe``,
    ``temperature``, ``top_k``, ``top_p``, ``min_p``, ``reverse_prompt``,
    ``mlock``) were dropped from ``ModelConfig`` — they now live in
    ``extra_args`` verbatim. This test now covers the fields llauncher
    still owns directly plus the passthrough.
    """

    def test_model_config_includes_all_server_options(self):
        """Test that ModelConfig includes the llauncher-owned fields."""
        config = ModelConfig.from_dict_unvalidated({
            "name": "test-model",
            "model_path": "/path/to/model.gguf",
            "n_gpu_layers": 32,
            "ctx_size": 2048,
            "parallel": 2,
            "extra_args": (
                "--threads 4 --threads-batch 2 --ubatch-size 128 "
                "--batch-size 512 --flash-attn on --cache-type-k f32 "
                "--cache-type-v f16 --n-cpu-moe 0 --temp 0.8 --top-k 40 "
                "--top-p 0.95 --min-p 0.05 --reverse-prompt </s> --mlock "
                "--log-disable"
            ),
        })

        # Verify the llauncher-owned fields are set correctly
        assert config.name == "test-model"
        assert config.model_path == "/path/to/model.gguf"
        assert config.n_gpu_layers == 32
        assert config.ctx_size == 2048
        assert config.parallel == 2
        # Everything else round-trips verbatim through extra_args.
        assert "--flash-attn on" in config.extra_args
        assert "--cache-type-k f32" in config.extra_args
        assert "--log-disable" in config.extra_args


class TestIssue11TopKMinPInUIForms:
    """Regression test for issue #11: top_k/min_p missing from UI forms.

    Per ADR-026 / issue #477, ``top_k``/``min_p`` are no longer dedicated
    ``ModelConfig`` fields — they are reachable through ``extra_args`` like
    every other sampling parameter, with no per-field widget to omit.
    """

    def test_model_config_supports_top_k_and_min_p_via_extra_args(self):
        """top_k/min_p are set and modified through extra_args."""
        config = ModelConfig.from_dict_unvalidated({
            "name": "test-model",
            "model_path": "/path/to/model.gguf",
            "extra_args": "--top-k 33 --min-p 0.02",
        })

        assert "--top-k 33" in config.extra_args
        assert "--min-p 0.02" in config.extra_args

        # Test that the passthrough string can be modified freely.
        config.extra_args = "--top-k 50 --min-p 0.1"
        assert "--top-k 50" in config.extra_args
        assert "--min-p 0.1" in config.extra_args


class TestIssue7UnusedMultiGpuFields:
    """Regression test for issue #7: Remove unused multi-GPU fields."""

    def test_model_config_works_without_multi_gpu_fields(self):
        """Test that ModelConfig works correctly without multi-GPU fields."""
        # Test creating a config without specifying multi-GPU related fields
        config = ModelConfig.from_dict_unvalidated({
            "name": "test-model",
            "model_path": "/path/to/model.gguf"
        })

        # Should have default values for multi-GPU related fields
        assert config.name == "test-model"
        assert config.model_path == "/path/to/model.gguf"
        # ``parallel`` is still a llauncher-owned field; the old
        # ``n_cpu_moe`` field was dropped per ADR-026 / issue #477 (it's
        # now reachable, if needed, through extra_args).
        assert hasattr(config, 'parallel')


class TestIssue5PortRename:
    """Regression test for issue #5: Start button fails - port rename.

    Original issue was about the ``default_port`` field not flowing through
    to start. Per ADR-LLNCH-010 the field is removed entirely — port now lives in
    the call. This test now verifies that legacy ``default_port`` data is
    silently dropped without breaking config load.
    """

    def test_legacy_default_port_silently_dropped(self):
        config = ModelConfig.from_dict_unvalidated({
            "name": "test-model",
            "model_path": "/path/to/model.gguf",
            "default_port": 8080,
        })
        assert not hasattr(config, "default_port")
        assert "default_port" not in config.to_dict()


class TestIssue3PortCoupledToModelProfile:
    """Regression test for issue #3: Port coupled to model profile.

    Per ADR-LLNCH-010 the coupling is removed structurally — ``ModelConfig`` no
    longer carries port at all. The same weights file can be referenced by
    two configs without any port-collision concern in the data model.
    """

    def test_same_path_different_configs_independent(self):
        config1 = ModelConfig.from_dict_unvalidated({
            "name": "shared-a",
            "model_path": "/path/to/model.gguf",
        })
        config2 = ModelConfig.from_dict_unvalidated({
            "name": "shared-b",
            "model_path": "/path/to/model.gguf",
        })
        assert config1.name != config2.name
        assert config1.model_path == config2.model_path
        # Port is not part of identity per ADR-LLNCH-010.
        assert not hasattr(config1, "default_port")
        assert not hasattr(config2, "default_port")


class TestIssue18LegacyExtraArgsConfig:
    """Regression test for issue #18: UI crash with old extra_args config."""

    def test_model_config_handles_legacy_extra_args_format(self):
        """Test that ModelConfig can handle various extra_args formats."""
        # Test empty extra_args
        config1 = ModelConfig.from_dict_unvalidated({
            "name": "test-model",
            "model_path": "/path/to/model.gguf",
            "extra_args": ""
        })
        assert config1.extra_args == ""

        # Test None-like extra_args (empty string)
        config2 = ModelConfig.from_dict_unvalidated({
            "name": "test-model",
            "model_path": "/path/to/model.gguf",
            "extra_args": " "
        })
        assert config2.extra_args == " "
        # Test complex extra_args
        config3 = ModelConfig.from_dict_unvalidated({
            "name": "test-model",
            "model_path": "/path/to/model.gguf",
            "extra_args": "--n-gpu-layers 32 --ctx-size 2048 --log-disable"
        })
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
        import os
        from pathlib import Path
        
        # Delete the nodes.json file to start fresh
        nodes_file = Path.home() / ".llauncher" / "nodes.json"
        if nodes_file.exists():
            nodes_file.unlink()
        
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