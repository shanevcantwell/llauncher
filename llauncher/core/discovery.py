"""Discover and parse llama-server launch scripts."""

import re
import shlex
from pathlib import Path
from typing import Iterator

from llauncher.models.config import ModelConfig


DEFAULT_SCRIPT_PATH = Path.home() / ".local" / "bin"


def discover_scripts(path: Path = DEFAULT_SCRIPT_PATH) -> list[ModelConfig]:
    """Discover launch scripts in the given directory.

    Looks for files matching launch-*.sh pattern.

    Args:
        path: Directory to search for scripts.

    Returns:
        List of ModelConfig objects parsed from scripts.
    """
    configs = []
    if not path.exists():
        return configs

    for script in path.glob("launch-*.sh"):
        config = parse_launch_script(script)
        if config:
            configs.append(config)

    return configs


def parse_launch_script(script: Path) -> ModelConfig | None:
    """Parse a launch script into a ModelConfig.

    Expects scripts in format:
    ```bash
    #!/bin/bash
    llama-server \
      -m /path/to/model.gguf \
      --mmproj /path/to/mmproj.gguf \
      --n-gpu-layers 255 \
      -c 131072 \
      ...
    ```

    Args:
        script: Path to the launch script.

    Returns:
        ModelConfig if parsing succeeds, None otherwise.
    """
    try:
        content = script.read_text()
    except (OSError, UnicodeError) as e:
        print(f"Error reading script {script}: {e}")
        return None

    # Extract the llama-server command line
    cmd_parts = _extract_command(content)
    if not cmd_parts:
        return None

    # Parse the command line arguments
    args = _parse_args(cmd_parts)
    if not args.get("-m") and not args.get("--model"):
        return None

    # Derive model name from script filename
    name = script.stem.replace("launch-", "")

    # Get model path (support both -m and --model)
    model_path = args.get("-m") or args.get("--model")

    config_data = {
        "name": name,
        "model_path": model_path,
        "mmproj_path": args.get("--mmproj"),
        "default_port": _get_port(args),
        "n_gpu_layers": int(args.get("--n-gpu-layers", 255)),
        "ctx_size": int(args.get("-c", args.get("--ctx-size", 131072))),
        "threads": _get_int(args, "--threads"),
        "threads_batch": int(args.get("--threads-batch", 8)),
        "ubatch_size": int(args.get("--ubatch-size", 512)),
        "batch_size": _get_int(args, "--batch-size", args.get("-b")),
        "flash_attn": args.get("--flash-attn", "on"),
        "no_mmap": "--no-mmap" in args,
        "cache_type_k": args.get("--cache-type-k", args.get("-ctk")),
        "cache_type_v": args.get("--cache-type-v", args.get("-ctv")),
        "n_cpu_moe": _get_int(args, "--n-cpu-moe", args.get("-ncmoe")),
        # Parallel/server slots
        "parallel": _get_parallel(args),
        # Sampling parameters
        "temperature": _get_float(args, "--temp"),
        "top_k": _get_int(args, "--top-k"),
        "top_p": _get_float(args, "--top-p"),
        "min_p": _get_float(args, "--min-p"),
        "reverse_prompt": args.get("-r", args.get("--reverse-prompt")),
        # Memory management
        "mlock": "--mlock" in args,
        "extra_args": [],
    }

    # Use unvalidated constructor for discovered scripts
    return ModelConfig.from_dict_unvalidated(config_data)


def _extract_command(content: str) -> list[str]:
    """Extract the llama-server command from script content."""
    lines = []
    in_command = False

    for line in content.splitlines():
        line = line.strip()

        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue

        # Start of command
        if "llama-server" in line or "llama-server" in line:
            in_command = True
            # Get part after llama-server
            idx = line.find("llama-server")
            line = line[idx + len("llama-server") :]

        if in_command:
            lines.append(line)

        # End of command (no backslash continuation)
        if not line.endswith("\\"):
            break

    # Join and clean up
    cmd = " ".join(lines).replace("\\", "").strip()
    return shlex.split(cmd) if cmd else []


def _parse_args(args: list[str]) -> dict[str, str | bool]:
    """Parse command line arguments into a dictionary."""
    result = {}
    i = 0

    while i < len(args):
        arg = args[i]

        if arg.startswith("--"):
            # Long option
            if "=" in arg:
                key, value = arg.split("=", 1)
                result[key] = value
            elif i + 1 < len(args) and not args[i + 1].startswith("-"):
                result[arg] = args[i + 1]
                i += 1
            else:
                result[arg] = True
        elif arg.startswith("-") and len(arg) == 2:
            # Short option
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                result[arg] = args[i + 1]
                i += 1
            else:
                result[arg] = True
        else:
            # Positional argument
            result[f"pos_{len([a for a in result if a.startswith('pos_')])}"] = arg

        i += 1

    return result


def _get_port(args: dict[str, str | bool]) -> int | None:
    """Extract port from args, return None if not specified."""
    port = args.get("--port")
    if port:
        return int(port)
    return None  # No default - will be auto-allocated at startup


def _get_int(args: dict[str, str | bool], key: str, alt_key: str | None = None) -> int | None:
    """Safely get an integer value from args."""
    value = args.get(key) or (args.get(alt_key) if alt_key else None)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _get_parallel(args: dict[str, str | bool]) -> int:
    """Get parallel slots from args, default to 1.

    This always returns an integer (never None) because parallel has a default.
    """
    # Check all possible keys for parallel setting
    for key in ["--parallel", "--n-parallel", "-np"]:
        value = args.get(key)
        if value is not None and not isinstance(value, bool):
            try:
                return int(value)
            except (ValueError, TypeError):
                pass
    return 1  # Default


def _get_float(args: dict[str, str | bool], key: str) -> float | None:
    """Safely get a float value from args."""
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def resolve_model_shards(first_shard: Path) -> list[Path]:
    """Resolve all shards for a sharded model.

    For a model like model-of-00005-of-00005.gguf, finds all related shards.

    Args:
        first_shard: Path to the first (or any) shard.

    Returns:
        List of all shard paths in order.
    """
    if not first_shard.exists():
        return []

    # Check if this is a sharded model
    if "-of-" not in first_shard.name:
        return [first_shard]

    # Pattern: name-of-NNNNN-of-NNNNN.gguf
    pattern = re.compile(
        r"^(.+)-of-(\d+)-of-(\d+)\.gguf$|^(.+)\.gguf$"
    )
    match = pattern.match(first_shard.name)

    if not match:
        return [first_shard]

    if match.group(4):
        # Non-sharded model matching end of pattern
        return [first_shard]

    base_name = match.group(1)
    total_shards = int(match.group(3))
    parent = first_shard.parent

    shards = []
    for i in range(total_shards):
        shard_num = str(i + 1).zfill(5)
        shard_name = f"{base_name}-of-{shard_num}-of-{total_shards}.gguf"
        shard_path = parent / shard_name
        if shard_path.exists():
            shards.append(shard_path)

    return shards if shards else [first_shard]
