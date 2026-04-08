"""Node registry for managing remote llauncher agents."""

import json
from pathlib import Path
from typing import Iterator

from llauncher.remote.node import RemoteNode, NodeStatus

NODES_FILE = Path.home() / ".llauncher" / "nodes.json"


class NodeRegistry:
    """Manages a collection of remote nodes.

    Persists node configurations to ~/.llauncher/nodes.json.
    """

    def __init__(self):
        self._nodes: dict[str, RemoteNode] = {}
        self._load()

    def __iter__(self) -> Iterator[RemoteNode]:
        """Iterate over all nodes."""
        return iter(self._nodes.values())

    def __len__(self) -> int:
        """Get the number of registered nodes."""
        return len(self._nodes)

    def _load(self) -> None:
        """Load nodes from the persistent file."""
        if not NODES_FILE.exists():
            return

        try:
            data = json.loads(NODES_FILE.read_text())
            for name, node_data in data.items():
                self._nodes[name] = RemoteNode(
                    name=node_data["name"],
                    host=node_data["host"],
                    port=node_data.get("port", 8765),
                    timeout=node_data.get("timeout", 5.0),
                )
        except (json.JSONDecodeError, KeyError):
            # Corrupted file, start fresh
            self._nodes.clear()

    def _save(self) -> None:
        """Save nodes to the persistent file."""
        NODES_FILE.parent.mkdir(parents=True, exist_ok=True)

        data = {}
        for name, node in self._nodes.items():
            data[name] = {
                "name": node.name,
                "host": node.host,
                "port": node.port,
                "timeout": node.timeout,
            }

        NODES_FILE.write_text(json.dumps(data, indent=2))

    def add_node(
        self,
        name: str,
        host: str,
        port: int = 8765,
        timeout: float = 5.0,
        overwrite: bool = False,
    ) -> tuple[bool, str]:
        """Add a node to the registry.

        Args:
            name: Unique name for this node.
            host: Hostname or IP address.
            port: Agent port.
            timeout: Connection timeout in seconds.
            overwrite: If True, overwrite existing node with same name.

        Returns:
            Tuple of (success, message).
        """
        if name in self._nodes and not overwrite:
            return False, f"Node '{name}' already exists. Use overwrite=True to replace."

        self._nodes[name] = RemoteNode(
            name=name,
            host=host,
            port=port,
            timeout=timeout,
        )
        self._save()
        return True, f"Node '{name}' added successfully"

    def remove_node(self, name: str) -> tuple[bool, str]:
        """Remove a node from the registry.

        Args:
            name: Name of the node to remove.

        Returns:
            Tuple of (success, message).
        """
        if name not in self._nodes:
            return False, f"Node '{name}' not found"

        del self._nodes[name]
        self._save()
        return True, f"Node '{name}' removed successfully"

    def get_node(self, name: str) -> RemoteNode | None:
        """Get a node by name.

        Args:
            name: Name of the node.

        Returns:
            The RemoteNode or None if not found.
        """
        return self._nodes.get(name)

    def refresh_all(self) -> dict[str, NodeStatus]:
        """Ping all nodes and update their status.

        Returns:
            Dictionary mapping node names to their status.
        """
        results = {}
        for name, node in self._nodes.items():
            node.ping()
            results[name] = node.status
        return results

    def get_online_nodes(self) -> list[RemoteNode]:
        """Get all nodes that are currently online.

        Returns:
            List of online RemoteNode instances.
        """
        return [node for node in self._nodes.values() if node.status == NodeStatus.ONLINE]

    def get_node_info_all(self) -> dict[str, dict]:
        """Get detailed info from all reachable nodes.

        Returns:
            Dictionary mapping node names to their info.
        """
        info = {}
        for name, node in self._nodes.items():
            node_info = node.get_node_info()
            if node_info:
                info[name] = node_info
        return info

    def to_dict(self) -> dict:
        """Convert registry to dictionary representation."""
        return {
            name: node.to_dict()
            for name, node in self._nodes.items()
        }
