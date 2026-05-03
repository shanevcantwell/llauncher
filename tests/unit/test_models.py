import pytest
from unittest.mock import patch
from llauncher.models.config import ModelConfig

def test_model_config_validation():
    """Test that ModelConfig validates input correctly."""
    # Using from_dict_unvalidated to skip path validation for this unit test
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "n_gpu_layers": 32,
        "ctx_size": 2048,
    }
    config = ModelConfig.from_dict_unvalidated(data)
    assert config.name == "test-model"
    assert config.n_gpu_layers == 32


def test_model_config_drops_legacy_default_port():
    """Per ADR-010, ``default_port`` is silently dropped on load."""
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "default_port": 8080,
        "n_gpu_layers": 32,
    }
    config = ModelConfig.from_dict_unvalidated(data)
    assert not hasattr(config, "default_port")
    assert "default_port" not in config.to_dict()


def test_model_config_extra_args_migration():
    """Test that extra_args is migrated from list[str] to str."""
    # Old format with list
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "extra_args": ["--flag1", "value1", "--flag2"],
    }
    config = ModelConfig.from_dict_unvalidated(data)
    # Should be migrated to space-separated string
    assert config.extra_args == "--flag1 value1 --flag2"


def test_model_config_extra_args_string_format():
    """Test that extra_args works with string format."""
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "extra_args": "--mcp-config /path/to/.mcp.json --flag value",
    }
    config = ModelConfig.from_dict_unvalidated(data)
    assert config.extra_args == "--mcp-config /path/to/.mcp.json --flag value"


def test_model_config_extra_args_empty():
    """Test that empty extra_args defaults to empty string."""
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
    }
    config = ModelConfig.from_dict_unvalidated(data)
    assert config.extra_args == ""


def test_model_config_extra_args_empty_list_migration():
    """Test that extra_args empty list [] is migrated to empty string "".

    Regression test for GitHub issue: UI crash when editing models with
    old-format config entries containing "extra_args": [].
    """
    # Old format with empty list (common in existing configs before the str migration)
    data = {
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "extra_args": [],  # Empty list from old format
    }
    config = ModelConfig.from_dict_unvalidated(data)
    assert config.extra_args == "", "Empty list should migrate to empty string"


class TestModelConfigFieldRoundtrip:
    """Test that all ModelConfig fields roundtrip correctly through to_dict/from_dict."""

    def test_all_fields_roundtrip(self):
        """Test all ModelConfig fields are preserved through serialization."""
        original_data = {
            "name": "test-model",
            "model_path": "/fake/path/model.gguf",
            "mmproj_path": "/fake/path/mmproj.gguf",
            "n_gpu_layers": 100,
            "ctx_size": 32768,
            "np": 4,
            "threads": 8,
            "threads_batch": 16,
            "ubatch_size": 1024,
            "batch_size": 512,
            "flash_attn": "auto",
            "no_mmap": True,
            "cache_type_k": "f16",
            "cache_type_v": "f16",
            "n_cpu_moe": 4,
            "parallel": 4,
            "temperature": 0.8,
            "top_k": 40,
            "top_p": 0.9,
            "min_p": 0.05,
            "reverse_prompt": "STOP",
            "mlock": True,
            "extra_args": "--custom-flag value",
        }

        config = ModelConfig.from_dict_unvalidated(original_data)
        serialized = config.to_dict()
        restored = ModelConfig.from_dict_unvalidated(serialized)

        # Verify all fields match
        assert restored.name == original_data["name"]
        assert restored.model_path == original_data["model_path"]
        assert restored.mmproj_path == original_data["mmproj_path"]
        assert restored.n_gpu_layers == original_data["n_gpu_layers"]
        assert restored.ctx_size == original_data["ctx_size"]
        assert restored.threads == original_data["threads"]
        assert restored.threads_batch == original_data["threads_batch"]
        assert restored.ubatch_size == original_data["ubatch_size"]
        assert restored.batch_size == original_data["batch_size"]
        assert restored.flash_attn == original_data["flash_attn"]
        assert restored.no_mmap == original_data["no_mmap"]
        assert restored.cache_type_k == original_data["cache_type_k"]
        assert restored.cache_type_v == original_data["cache_type_v"]
        assert restored.n_cpu_moe == original_data["n_cpu_moe"]
        assert restored.parallel == original_data["parallel"]
        assert restored.temperature == original_data["temperature"]
        assert restored.top_k == original_data["top_k"]
        assert restored.top_p == original_data["top_p"]
        assert restored.min_p == original_data["min_p"]
        assert restored.reverse_prompt == original_data["reverse_prompt"]
        assert restored.mlock == original_data["mlock"]
        assert restored.extra_args == original_data["extra_args"]
        assert restored.np == original_data["np"]

    def test_optional_fields_defaults(self):
        """Test optional fields have correct defaults when not specified."""
        minimal_data = {
            "name": "minimal-model",
            "model_path": "/fake/path/model.gguf",
        }
        config = ModelConfig.from_dict_unvalidated(minimal_data)

        # Check defaults
        assert config.mmproj_path is None
        assert config.n_gpu_layers == 255  # Default
        assert config.ctx_size == 131072  # Default
        assert config.threads is None
        assert config.threads_batch == 8  # Default
        assert config.ubatch_size == 512  # Default
        assert config.batch_size is None
        assert config.flash_attn == "on"  # Default
        assert config.no_mmap is False
        assert config.cache_type_k is None
        assert config.cache_type_v is None
        assert config.n_cpu_moe is None
        assert config.parallel == 1  # Default
        assert config.temperature is None
        assert config.top_k is None
        assert config.top_p is None
        assert config.min_p is None
        assert config.reverse_prompt is None
        assert config.mlock is False
        assert config.extra_args == ""
        assert config.np is None


class TestModelConfigLegacyFieldDrop:
    """Per ADR-010 + v2 migration policy, legacy port-related fields are
    silently dropped on load. The data isn't precious; user re-specifies."""

    def test_legacy_port_field_dropped(self):
        old_format = {
            "name": "old-model",
            "model_path": "/fake/path/model.gguf",
            "port": 9090,
        }
        config = ModelConfig.from_dict_unvalidated(old_format)
        assert not hasattr(config, "port")
        assert "port" not in config.to_dict()

    def test_legacy_default_port_field_dropped(self):
        old_format = {
            "name": "mixed-model",
            "model_path": "/fake/path/model.gguf",
            "default_port": 8080,
        }
        config = ModelConfig.from_dict_unvalidated(old_format)
        assert not hasattr(config, "default_port")
        assert "default_port" not in config.to_dict()

    def test_host_field_ignored(self):
        """Test that old 'host' field is dropped without error."""
        old_format = {
            "name": "old-model",
            "model_path": "/fake/path/model.gguf",
            "host": "127.0.0.1",  # Old field, should be dropped
        }
        config = ModelConfig.from_dict_unvalidated(old_format)
        # Should not raise, and host should not be in output
        assert "host" not in config.to_dict()


class TestModelConfigFieldValidators:
    """Test field validators in ModelConfig."""

    def test_n_gpu_layers_valid(self):
        """Test valid n_gpu_layers values."""
        for value in [0, 1, 100, 255, 1024]:
            data = {
                "name": "test-model",
                "model_path": "/fake/path/model.gguf",
                "n_gpu_layers": value,
            }
            config = ModelConfig.from_dict_unvalidated(data)
            assert config.n_gpu_layers == value

    def test_n_gpu_layers_invalid(self):
        """Test invalid n_gpu_layers raises error."""
        data = {
            "name": "test-model",
            "model_path": "/fake/path/model.gguf",
            "n_gpu_layers": -1,
        }
        with pytest.raises(ValueError):
            ModelConfig.from_dict_unvalidated(data)

    def test_ctx_size_valid(self):
        """Test valid ctx_size values."""
        for value in [1, 1024, 131072, 262144]:
            data = {
                "name": "test-model",
                "model_path": "/fake/path/model.gguf",
                "ctx_size": value,
            }
            config = ModelConfig.from_dict_unvalidated(data)
            assert config.ctx_size == value

    def test_ctx_size_invalid(self):
        """Test invalid ctx_size raises error."""
        data = {
            "name": "test-model",
            "model_path": "/fake/path/model.gguf",
            "ctx_size": 0,
        }
        with pytest.raises(ValueError):
            ModelConfig.from_dict_unvalidated(data)

    def test_np_valid_values(self):
        """Test valid np values are accepted."""
        for value in [1, 2, 4, 8]:
            data = {
                "name": "test-model",
                "model_path": "/fake/path/model.gguf",
                "np": value,
            }
            config = ModelConfig.from_dict_unvalidated(data)
            assert config.np == value

    def test_np_invalid_values(self):
        """Test invalid np values raise errors."""
        for value in [0, -1]:
            data = {
                "name": "test-model",
                "model_path": "/fake/path/model.gguf",
                "np": value,
            }
            with pytest.raises(ValueError):
                ModelConfig.from_dict_unvalidated(data)

    def test_np_defaults_to_none(self):
        """Test np defaults to None when not specified."""
        data = {
            "name": "test-model",
            "model_path": "/fake/path/model.gguf",
        }
        config = ModelConfig.from_dict_unvalidated(data)
        assert config.np is None

    def test_np_none_serializes_to_none(self):
        """Test that np=None serializes to null/None in to_dict()."""
        data = {
            "name": "test-model",
            "model_path": "/fake/path/model.gguf",
        }
        config = ModelConfig.from_dict_unvalidated(data)
        serialized = config.to_dict()
        assert serialized["np"] is None

    def test_flash_attn_valid_values(self):
        """Test valid flash_attn values."""
        for value in ["on", "off", "auto"]:
            data = {
                "name": "test-model",
                "model_path": "/fake/path/model.gguf",
                "flash_attn": value,
            }
            config = ModelConfig.from_dict_unvalidated(data)
            assert config.flash_attn == value

    def test_flash_attn_invalid_value(self):
        """Test invalid flash_attn raises error."""
        data = {
            "name": "test-model",
            "model_path": "/fake/path/model.gguf",
            "flash_attn": "invalid",
        }
        with pytest.raises(ValueError):
            ModelConfig.from_dict_unvalidated(data)

    def test_cache_type_valid_values(self):
        """Test valid cache_type values."""
        for value in ["f32", "f16", "bf16", "q8_0"]:
            data = {
                "name": "test-model",
                "model_path": "/fake/path/model.gguf",
                "cache_type_k": value,
                "cache_type_v": value,
            }
            config = ModelConfig.from_dict_unvalidated(data)
            assert config.cache_type_k == value
            assert config.cache_type_v == value

    def test_cache_type_invalid_value(self):
        """Test invalid cache_type raises error."""
        data = {
            "name": "test-model",
            "model_path": "/fake/path/model.gguf",
            "cache_type_k": "invalid",
        }
        with pytest.raises(ValueError):
            ModelConfig.from_dict_unvalidated(data)
