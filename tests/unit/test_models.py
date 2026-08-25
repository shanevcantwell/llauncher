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
    """Per ADR-LLNCH-010, ``default_port`` is silently dropped on load."""
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
    """Test that all ModelConfig fields roundtrip correctly through to_dict/from_dict.

    Per ADR-026 / issue #477, the 16 llama-server mirror fields
    (``threads``, ``threads_batch``, ``ubatch_size``, ``batch_size``,
    ``flash_attn``, ``no_mmap``, ``cache_type_k/v``, ``n_cpu_moe``,
    ``temperature``, ``top_k``, ``top_p``, ``min_p``, ``repeat_penalty``,
    ``reverse_prompt``, ``mlock``) no longer exist as ``ModelConfig``
    fields — the equivalent flags now round-trip through ``extra_args``.
    """

    def test_all_fields_roundtrip(self):
        """Test all ModelConfig fields are preserved through serialization."""
        original_data = {
            "name": "test-model",
            "model_path": "/fake/path/model.gguf",
            "mmproj_path": "/fake/path/mmproj.gguf",
            "n_gpu_layers": 100,
            "ctx_size": 32768,
            "parallel": 4,
            "metrics": False,
            "slots": True,
            "extra_args": (
                "--threads 8 --threads-batch 16 --ubatch-size 1024 "
                "--batch-size 512 --flash-attn auto --no-mmap "
                "--cache-type-k f16 --cache-type-v f16 --n-cpu-moe 4 "
                "--temp 0.8 --top-k 40 --top-p 0.9 --min-p 0.05 "
                "--reverse-prompt STOP --mlock --custom-flag value"
            ),
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
        assert restored.parallel == original_data["parallel"]
        assert restored.metrics == original_data["metrics"]
        assert restored.slots == original_data["slots"]
        assert restored.extra_args == original_data["extra_args"]

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
        assert config.parallel == 1  # Default
        assert config.metrics is True  # Default
        assert config.slots is False  # Default
        assert config.extra_args == ""


class TestModelConfigLegacyFieldDrop:
    """Per ADR-LLNCH-010 + v2 migration policy, legacy port-related fields are
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

    def test_np_field_dropped(self):
        """Issue #235: ``np`` was a dead, mislabeled duplicate of
        ``parallel`` — never rendered by ``build_command``. It is removed
        entirely; a persisted config carrying a (always-null, per live
        store audit) legacy ``np`` key loads without error and drops it."""
        old_format = {
            "name": "old-model",
            "model_path": "/fake/path/model.gguf",
            "np": 4,
        }
        config = ModelConfig.from_dict_unvalidated(old_format)
        assert not hasattr(config, "np")
        assert "np" not in config.to_dict()


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

    def test_flash_attn_any_value_passes_through_extra_args(self):
        """Per ADR-026 / issue #477, ``flash_attn`` is no longer a typed
        field with a ``Literal`` constraint — it's a free-form extra_args
        token, so even a value llama-server itself would reject is
        accepted by ModelConfig (no pydantic content validation)."""
        for value in ["on", "off", "auto", "invalid-but-unvalidated"]:
            data = {
                "name": "test-model",
                "model_path": "/fake/path/model.gguf",
                "extra_args": f"--flash-attn {value}",
            }
            config = ModelConfig.from_dict_unvalidated(data)
            assert f"--flash-attn {value}" in config.extra_args

    def test_cache_type_any_value_passes_through_extra_args(self):
        """Per ADR-026 / issue #477 (the issue's root cause): the former
        ``Literal["f32", "f16", "bf16", "q8_0"]`` on ``cache_type_k/v``
        could not hold ``q4_0``, a value llama-server actually supports.
        extra_args has no such ceiling."""
        for value in ["f32", "f16", "bf16", "q8_0", "q4_0"]:
            data = {
                "name": "test-model",
                "model_path": "/fake/path/model.gguf",
                "extra_args": f"--cache-type-k {value} --cache-type-v {value}",
            }
            config = ModelConfig.from_dict_unvalidated(data)
            assert f"--cache-type-k {value}" in config.extra_args
            assert f"--cache-type-v {value}" in config.extra_args
