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
    assert retrieved.model_path == sample_model_config.model_path
    assert retrieved.n_gpu_layers == sample_model_config.n_gpu_layers

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


def test_config_store_update_model(mock_config_store, sample_model_config):
    """Test updating an existing model."""
    ConfigStore.add_model(sample_model_config)

    updated_config = sample_model_config.model_copy(update={"ctx_size": 8192})
    ConfigStore.update_model(sample_model_config.name, updated_config)

    retrieved = ConfigStore.get_model(sample_model_config.name)
    assert retrieved.ctx_size == 8192

def test_config_store_update_name_mismatch(mock_config_store, sample_model_config):
    """Test that updating with a name mismatch raises ValueError."""
    ConfigStore.add_model(sample_model_config)

    mismatched_config = sample_model_config.model_copy(update={"name": "wrong-name"})
    with pytest.raises(ValueError, match="Name mismatch"):
        ConfigStore.update_model(sample_model_config.name, mismatched_config)


# ---------------------------------------------------------------------------
# Issue #60 — ConfigStore CRUD emits audit-log entries per ADR-008
# ---------------------------------------------------------------------------


def _read_audit(tmp_config_dir: Path) -> list[dict]:
    import json
    path = tmp_config_dir / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_add_model_emits_audit_entry(
    mock_config_store, sample_model_config, tmp_config_dir
):
    """Adding a model writes a model_added audit entry."""
    ConfigStore.add_model(sample_model_config, caller="ui")

    entries = _read_audit(tmp_config_dir)
    assert len(entries) == 1
    e = entries[0]
    assert e["action"] == "model_added"
    assert e["result"] == "success"
    assert e["caller"] == "ui"
    assert e["model"] == sample_model_config.name


def test_update_model_emits_audit_entry_with_changed_fields(
    mock_config_store, sample_model_config, tmp_config_dir
):
    """Updating a model writes a model_updated entry whose message lists changed fields."""
    ConfigStore.add_model(sample_model_config, caller="ui")

    updated = sample_model_config.model_copy(
        update={"ctx_size": 8192, "n_gpu_layers": 100}
    )
    ConfigStore.update_model(sample_model_config.name, updated, caller="cli")

    entries = _read_audit(tmp_config_dir)
    # First entry is the add; second is the update we are asserting on.
    assert len(entries) == 2
    e = entries[1]
    assert e["action"] == "model_updated"
    assert e["result"] == "success"
    assert e["caller"] == "cli"
    assert e["model"] == sample_model_config.name
    # Both changed fields should be named in the message; order is sorted.
    assert "ctx_size" in e["message"]
    assert "n_gpu_layers" in e["message"]


def test_update_model_no_field_changes_records_no_op_message(
    mock_config_store, sample_model_config, tmp_config_dir
):
    """A no-op update still emits an entry, with a 'no field changes' message."""
    ConfigStore.add_model(sample_model_config, caller="ui")
    ConfigStore.update_model(
        sample_model_config.name, sample_model_config, caller="ui"
    )

    entries = _read_audit(tmp_config_dir)
    assert entries[1]["message"] == "no field changes"


def test_remove_model_emits_audit_entry(
    mock_config_store, sample_model_config, tmp_config_dir
):
    """Removing an existing model writes a model_removed entry."""
    ConfigStore.add_model(sample_model_config, caller="ui")
    ConfigStore.remove_model(sample_model_config.name, caller="mcp")

    entries = _read_audit(tmp_config_dir)
    assert len(entries) == 2
    e = entries[1]
    assert e["action"] == "model_removed"
    assert e["result"] == "success"
    assert e["caller"] == "mcp"
    assert e["model"] == sample_model_config.name


def test_remove_missing_model_is_silent_no_audit(
    mock_config_store, tmp_config_dir
):
    """Removing a non-existent model is a no-op and emits no audit entry."""
    ConfigStore.remove_model("does-not-exist", caller="ui")
    assert _read_audit(tmp_config_dir) == []


def test_update_name_mismatch_emits_no_audit(
    mock_config_store, sample_model_config, tmp_config_dir
):
    """Update rejected for name mismatch must not write an audit entry."""
    ConfigStore.add_model(sample_model_config, caller="ui")
    mismatched = sample_model_config.model_copy(update={"name": "wrong"})
    with pytest.raises(ValueError):
        ConfigStore.update_model(sample_model_config.name, mismatched, caller="ui")

    entries = _read_audit(tmp_config_dir)
    # Only the add; the rejected update did not write an entry.
    assert len(entries) == 1
    assert entries[0]["action"] == "model_added"


def test_caller_defaults_to_unknown(
    mock_config_store, sample_model_config, tmp_config_dir
):
    """Callers that don't pass a caller= are recorded as 'unknown'."""
    ConfigStore.add_model(sample_model_config)
    entries = _read_audit(tmp_config_dir)
    assert entries[0]["caller"] == "unknown"
