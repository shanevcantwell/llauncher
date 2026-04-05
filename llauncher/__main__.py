"""Entry points for llauncher."""

import argparse
import sys
from pathlib import Path


def main():
    """Main entry point for llauncher CLI."""
    parser = argparse.ArgumentParser(description="llauncher - llama.cpp server launcher")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    parser.add_argument(
        "command",
        choices=["discover", "mcp", "ui"],
        help="Command to run",
    )

    args = parser.parse_args()

    if args.command == "discover":
        from llauncher.core.discovery import discover_scripts

        configs = discover_scripts()
        for config in configs:
            print(f"\n{config.name}:")
            print(f"  Model: {config.model_path}")
            print(f"  Port: {config.port}")
            print(f"  GPU Layers: {config.n_gpu_layers}")
            print(f"  Context: {config.ctx_size}")

    elif args.command == "mcp":
        from llauncher.mcp.server import main as mcp_main

        mcp_main()

    elif args.command == "ui":
        print("Use 'llauncher-ui' command or 'streamlit run llauncher/ui/app.py'")
        sys.exit(1)


if __name__ == "__main__":
    main()
