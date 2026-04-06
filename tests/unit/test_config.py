import pytest
from pathlib import Path
from unittest.mock import patch
from llauncher.core.config import ConfigStore
from llauncher.models.config import ModelConfig

def test_config_store_add_and_get(mock_config_store, sample_model_config):
    """Test adding and retrieving a model from ConfigStore."""
    # We use mock_config_store fixture which patches CONFIG_DIR and CONFIG_PATH
    ConfigStore.add_model(sample_model_config)

    retrieved = ConfigStore.get_model(sample_model_config.name)
    assert retrieved is not None
    assert retrieved.name == sample_model_config.name
    assert retrieved.default_port == sample_model_config.default_port

def test_config_store_remove(mock_config_store, sample_model_config):
    """Test removing a model from ConfigStore."""
    ConfigStore.add_model(sample_model_config)
    assert ConfigStore.get_model(sample_model_config.name) is not None

    ConfigStore.remove_model(sample_model_config.name)
    assert ConfigStore.get_model(sample_model_config.name) is None

def test_config_store_list_models(mock_config_store, sample_model_config):
    """Test listing model names."""
    config2 = sample_model_config.model_copy(update={"name": "model2"})

    ConfigStore.add_model(sample_model_config)
    ConfigStore.add_model(config2)

    models = ConfigStore.list_models()
    assert len(models) == 2
    assert sample_model_config.name in models
    assert config2.name in models

def test_config_store_load_nonexistent(mock_config_store):
    """Test loading when no config file exists."""
    # CONFIG_PATH is mocked to a non-existent file in tmp_config_dir
    models = ConfigStore.load()
    assert models == {}

def test_config_store_merge_discovered(mock_config_store, sample_model_config):
    """Test merging discovered scripts with persisted configs."""
    # Persist one model
    ConfigStore.add_model(sample_model_config)

    # Discovered model (different name)
    discovered_config = sample_model_config.model_copy(update={"name": "discovered-model"})

    merged = ConfigStore.merge_discovered([discovered_config])

    assert sample_model_config.name in merged
    assert discovered_config.name in merged
    assert len(merged) == 2

def test_config_store_update_model(mock_config_store, sample_model_config):
    """Test updating an existing model."""
    ConfigStore.add_model(sample_model_config)

    updated_config = sample_model_config.model_copy(update={"default_port": 9090})
    ConfigStore.update_model(sample_model_config.name, updated_config)

    retrieved = ConfigStore.get_model(sample_model_config.name)
    assert retrieved.default_port == 9090

def test_config_store_update_name_mismatch(mock_config_store, sample_model_config):
    """Test that updating with a name mismatch raises ValueError."""
    ConfigStore.add_model(sample_model_config)

    mismatched_config = sample_model_config.model_copy(update={"name": "wrong-name"})
    with pytest.raises(ValueError, match="Name mismatch"):
        ConfigStore.update_model(sample_model_config.name, mismatched_config)
