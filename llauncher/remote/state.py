"""Aggregated state management across multiple remote nodes."""

from llauncher.remote.node import RemoteNode, RemoteServerInfo
from llauncher.remote.registry import NodeRegistry
from llauncher.models.config import ModelConfig


class RemoteAggregator:
    """Aggregates state and actions across multiple llauncher nodes.

    Provides a unified interface for:
    - Viewing all servers across all nodes
    - Starting/stopping servers on specific nodes
    - Listing models available on each node
    """

    def __init__(self, registry: NodeRegistry | None = None):
        """Initialize the aggregator.

        Args:
            registry: NodeRegistry instance. Creates one if not provided.
        """
        self.registry = registry or NodeRegistry()

    def get_all_servers(self) -> list[RemoteServerInfo]:
        """Get all running servers across all nodes.

        Returns:
            List of RemoteServerInfo from all online nodes.
        """
        servers = []

        for node in self.registry:
            status = node.get_status()
            if status is None:
                continue

            for server_data in status.get("running_servers", []):
                server = RemoteServerInfo(
                    node_name=node.name,
                    pid=server_data["pid"],
                    port=server_data["port"],
                    config_name=server_data["config_name"],
                    start_time=server_data["start_time"],
                    uptime_seconds=server_data["uptime_seconds"],
                    logs_path=server_data.get("logs_path"),
                )
                servers.append(server)

        return servers

    def get_all_models(self) -> dict[str, list[dict]]:
        """Get all configured models from all nodes.

        Returns:
            Dictionary mapping node names to their model lists.
        """
        models_by_node = {}

        for node in self.registry:
            models = node.get_models()
            if models is not None:
                models_by_node[node.name] = models

        return models_by_node

    def get_models_by_name(self) -> dict[str, list[tuple[str, dict]]]:
        """Get models grouped by model name across nodes.

        Returns:
            Dictionary mapping model names to list of (node_name, model_data) tuples.
        """
        models_by_name: dict[str, list[tuple[str, dict]]] = {}

        for node in self.registry:
            models = node.get_models()
            if models is None:
                continue

            for model in models:
                name = model["name"]
                if name not in models_by_name:
                    models_by_name[name] = []
                models_by_name[name].append((node.name, model))

        return models_by_name

    def start_on_node(
        self,
        node_name: str,
        model_name: str,
    ) -> dict | None:
        """Start a server on a specific node.

        Args:
            node_name: Name of the node.
            model_name: Name of the model to start.

        Returns:
            Result dictionary or None if node not found.
        """
        node = self.registry.get_node(node_name)
        if node is None:
            return {"success": False, "error": f"Node '{node_name}' not found"}

        return node.start_server(model_name)

    def stop_on_node(
        self,
        node_name: str,
        port: int,
    ) -> dict | None:
        """Stop a server on a specific node.

        Args:
            node_name: Name of the node.
            port: Port of the server to stop.

        Returns:
            Result dictionary or None if node not found.
        """
        node = self.registry.get_node(node_name)
        if node is None:
            return {"success": False, "error": f"Node '{node_name}' not found"}

        return node.stop_server(port)

    def get_logs_on_node(
        self,
        node_name: str,
        port: int,
        lines: int = 100,
    ) -> list[str] | None:
        """Get logs for a server on a specific node.

        Args:
            node_name: Name of the node.
            port: Port of the server.
            lines: Number of lines to retrieve.

        Returns:
            List of log lines or None if failed.
        """
        node = self.registry.get_node(node_name)
        if node is None:
            return None

        return node.get_logs(port, lines)

    def refresh_all_nodes(self) -> dict[str, str]:
        """Refresh status of all nodes.

        Returns:
            Dictionary mapping node names to status strings.
        """
        status = self.registry.refresh_all()
        return {name: s.value for name, s in status.items()}

    def get_summary(self) -> dict:
        """Get a summary of the entire multi-node deployment.

        Returns:
            Summary dictionary with counts and status.
        """
        self.registry.refresh_all()

        online_nodes = self.registry.get_online_nodes()
        all_servers = self.get_all_servers()
        all_models = self.get_all_models()

        return {
            "total_nodes": len(self.registry),
            "online_nodes": len(online_nodes),
            "offline_nodes": len(self.registry) - len(online_nodes),
            "total_servers": len(all_servers),
            "total_models": sum(len(m) for m in all_models.values()),
            "nodes": [node.to_dict() for node in self.registry],
            "servers": [s.to_dict() for s in all_servers],
        }
