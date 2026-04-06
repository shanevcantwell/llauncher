import pytest
from pathlib import Path
from llauncher.core.discovery import (
    discover_scripts,
    parse_launch_script,
    _extract_command,
    _parse_args,
    resolve_model_shards
)

def test_extract_command_basic():
    content = """
    #!/bin/bash
    llama-server \\
      -m /path/to/model.gguf \\
      --port 8080
    """
    cmd = _extract_command(content)
    assert cmd == ["-m", "/path/to/model.gguf", "--port", "8080"]

def test_extract_command_no_backslash():
    content = """
    llama-server -m model.gguf --port 8080
    """
    cmd = _extract_command(content)
    assert cmd == ["-m", "model.gguf", "--port", "8080"]

def test_parse_args_long_option():
    args = ["--model", "path/to/model.gguf", "--port", "8080"]
    parsed = _parse_args(args)
    assert parsed["--model"] == "path/to/model.gguf"
    assert parsed["--port"] == "8080"

def test_parse_args_equals():
    args = ["--model=path/to/model.gguf", "--port=8080"]
    parsed = _parse_args(args)
    assert parsed["--model"] == "path/to/model.gguf"
    assert parsed["--port"] == "8080"

def test_parse_args_boolean():
    args = ["--no-mmap", "--flash-attn", "on"]
    parsed = _parse_args(args)
    assert parsed["--no-mmap"] is True
    assert parsed["--flash-attn"] == "on"

def test_parse_args_short_option():
    args = ["-m", "model.gguf", "-c", "2048"]
    parsed = _parse_args(args)
    assert parsed["-m"] == "model.gguf"
    assert parsed["-c"] == "2048"

def test_parse_launch_script_success(tmp_path):
    script = tmp_path / "launch-test-model.sh"
    script.write_text("""
#!/bin/bash
llama-server \\
  -m /path/to/model.gguf \\
  --port 8081 \\
  -c 4096
""")
    # Mock existence for ModelConfig validation if needed,
    # but parse_launch_script uses from_dict_unvalidated
    config = parse_launch_script(script)
    assert config is not None
    assert config.name == "test-model"
    assert config.model_path == "/path/to/model.gguf"
    assert config.port == 8081
    assert config.ctx_size == 4096

def test_discover_scripts(tmp_path):
    # Create a dummy script
    script = tmp_path / "launch-my-model.sh"
    script.write_text("llama-server -m /path/to/model.gguf")

    configs = discover_scripts(tmp_path)
    assert len(configs) == 1
    assert configs[0].name == "my-model"

def test_resolve_model_shards_non_sharded(tmp_path):
    shard = tmp_path / "model.gguf"
    shard.touch()
    resolved = resolve_model_shards(shard)
    assert resolved == [shard]

def test_resolve_model_shards_missing(tmp_path):
    shard = tmp_path / "nonexistent.gguf"
    resolved = resolve_model_shards(shard)
    assert resolved == []
