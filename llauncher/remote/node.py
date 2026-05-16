"""Remote node client for connecting to llauncher agents.

Per ADR-009 the topology is symmetric: every node runs an agent and
every node also acts as a client of peer agents. The same
:class:`RemoteNode` abstraction is therefore used to talk to the
*local* node too — but routing local calls over HTTP-loopback wastes
a TCP roundtrip, an auth hop, and an introduces a spurious failure
mode (agent down). Per issue #62, verb methods detect the self-loop
case (``_is_self_loop``) and route through the in-process
``llauncher.operations`` package directly. Auth is enforced only at
the network boundary (see ``agent.middleware``); the in-process path
intentionally skips it.
"""

import logging
import os
import socket
from datetime import datetime
from enum import Enum
from typing import Literal

import httpx

logger = logging.getLogger(__name__)


def _local_host_names() -> frozenset[str]:
    """Hostnames + addresses that resolve to this machine.

    ``socket.gethostname()`` can raise on misconfigured systems; fall
    back gracefully so a registry lookup never blows up here.
    """
    names: set[str] = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    try:
        names.add(socket.gethostname())
    except OSError:
        logger.debug("socket.gethostname() failed; falling back to literal set")
    return frozenset(names)


def _local_agent_port() -> int:
    """The agent port the local node is configured to bind."""
    return int(os.getenv("LAUNCHER_AGENT_PORT", "8765"))


class NodeStatus(Enum):
    """Status of a remote node."""

    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"


class RemoteServerInfo:
    """Information about a server running on a remote node."""

    def __init__(
        self,
        node_name: str,
        pid: int,
        port: int,
        config_name: str,
        start_time: str,
        uptime_seconds: int,
        logs_path: str | None = None,
    ):
        self.node_name = node_name
        self.pid = pid
        self.port = port
        self.config_name = config_name
        self.start_time = start_time
        self.uptime_seconds = uptime_seconds
        self.logs_path = logs_path

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "node_name": self.node_name,
            "pid": self.pid,
            "port": self.port,
            "config_name": self.config_name,
            "start_time": self.start_time,
            "uptime_seconds": self.uptime_seconds,
            "logs_path": self.logs_path,
        }


class RemoteNode:
    """Client for connecting to a remote llauncher agent.

    Attributes:
        name: User-friendly name for this node.
        host: Hostname or IP address of the agent.
        port: Port the agent is listening on.
        status: Current connection status.
        last_seen: Last successful ping time.
    """

    def __init__(
        self,
        name: str,
        host: str,
        port: int = 8765,
        timeout: float = 5.0,
        api_key: str | None = None,
    ):
        self.name = name
        self.host = host
        self.port = port
        self.timeout = timeout
        self.api_key: str | None = api_key if api_key else None
        self.status = NodeStatus.OFFLINE
        self.last_seen: datetime | None = None
        self._error_message: str | None = None

    @property
    def base_url(self) -> str:
        """Get the base URL for this node's agent."""
        return f"http://{self.host}:{self.port}"

    def __str__(self) -> str:
        return f"RemoteNode({self.name}@{self.host}:{self.port}, status={self.status.value})"

    def _get_headers(self) -> dict[str, str]:
        """Get request headers, including X-Api-Key if configured on the node.

        Returns:
            Dictionary of HTTP headers to include in requests.
        """
        headers: dict[str, str] = {}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    def _get_client(self) -> httpx.Client:
        """Create an HTTP client configured for this node."""
        return httpx.Client(timeout=self.timeout)

    def _is_self_loop(self) -> bool:
        """Return True if this node points back at the local agent.

        The detection is conservative — over-reporting "self" would
        route remote calls in-process and silently lose multi-node
        isolation, which is much worse than the cost of one extra
        loopback HTTP call. Two independent signals are accepted:

        * ``self.name == "local"`` — the registry convention from
          :mod:`llauncher.remote.registry`.
        * ``self.host in {localhost, 127.0.0.1, ::1, 0.0.0.0, this
          machine's hostname}`` AND ``self.port`` matches the local
          agent's bind port.

        The host+port conjunction guards against the case where a
        user has renamed the local node away from ``"local"``.
        """
        if self.name == "local":
            return True
        return (
            self.host in _local_host_names()
            and self.port == _local_agent_port()
        )

    def ping(self) -> bool:
        """Check if the node's agent is reachable.

        Returns:
            True if the agent responded, False otherwise.
        """
        if self._is_self_loop():
            # In-process — by definition reachable. Mirror the bookkeeping
            # the HTTP path does on a successful ping.
            self.status = NodeStatus.ONLINE
            self.last_seen = datetime.now()
            return True
        try:
            with self._get_client() as client:
                response = client.get(
                    f"{self.base_url}/health",
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    self.status = NodeStatus.ONLINE
                    self.last_seen = datetime.now()
                    self._error_message = None
                    return True
                else:
                    self.status = NodeStatus.ERROR
                    self._error_message = f"Unexpected status: {response.status_code}"
                    return False
        except httpx.RequestError as e:
            self.status = NodeStatus.OFFLINE
            self._error_message = str(e)
            return False

    def get_node_info(self) -> dict | None:
        """Get detailed information about the node.

        Returns:
            Node info dictionary or None if unavailable.
        """
        try:
            with self._get_client() as client:
                response = client.get(
                    f"{self.base_url}/node-info",
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    self.status = NodeStatus.ONLINE
                    self.last_seen = datetime.now()
                    return response.json()
                return None
        except httpx.RequestError:
            self.status = NodeStatus.OFFLINE
            return None

    def get_status(self) -> dict | None:
        """Get the current status of running servers on this node.

        Returns:
            Status dictionary or None if unavailable.
        """
        try:
            with self._get_client() as client:
                response = client.get(
                    f"{self.base_url}/status",
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    self.status = NodeStatus.ONLINE
                    self.last_seen = datetime.now()
                    return response.json()
                return None
        except httpx.RequestError:
            self.status = NodeStatus.OFFLINE
            return None

    def get_models(self) -> list[dict] | None:
        """List all configured models on this node.

        Returns:
            List of model configurations or None if unavailable.
        """
        try:
            with self._get_client() as client:
                response = client.get(
                    f"{self.base_url}/models",
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    self.status = NodeStatus.ONLINE
                    self.last_seen = datetime.now()
                    return response.json()
                return None
        except httpx.RequestError:
            self.status = NodeStatus.OFFLINE
            return None

    def start_server(self, model_name: str, port: int) -> dict | None:
        """Start ``model_name`` on ``port`` on this node (ADR-010).

        Per ADR-010, port is supplied at the call site. The HTTP body
        carries the model name; the path carries the port.

        Returns:
            The agent's structured ``StartResult`` dict on 2xx, or
            ``{"success": False, "error": ...}`` on transport or HTTP
            error. The error dict surfaces the agent's ``action`` field
            when available so callers can distinguish a 409 from a 500.
        """
        if self._is_self_loop():
            from llauncher import operations as ops

            self.status = NodeStatus.ONLINE
            self.last_seen = datetime.now()
            return ops.start(model_name, port, caller="local").to_dict()
        try:
            with self._get_client() as client:
                response = client.post(
                    f"{self.base_url}/start/{port}",
                    json={"model": model_name},
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    self.status = NodeStatus.ONLINE
                    self.last_seen = datetime.now()
                    return response.json()
                # FastAPI HTTPException wraps the structured op result
                # under "detail"; surface it verbatim when present.
                detail = None
                try:
                    detail = response.json().get("detail")
                except Exception:
                    pass
                if isinstance(detail, dict):
                    return {"success": False, "error": detail.get("message", "error"), **detail}
                return {
                    "success": False,
                    "error": detail or f"HTTP {response.status_code}",
                }
        except httpx.RequestError as e:
            self.status = NodeStatus.OFFLINE
            return {"success": False, "error": str(e)}

    def swap_server(self, model_name: str, port: int) -> dict | None:
        """Swap the model on ``port`` to ``model_name`` per ADR-011."""
        if self._is_self_loop():
            from llauncher import operations as ops

            self.status = NodeStatus.ONLINE
            self.last_seen = datetime.now()
            return ops.swap(model_name, port, caller="local").to_dict()
        try:
            with self._get_client() as client:
                response = client.post(
                    f"{self.base_url}/swap/{port}",
                    json={"model": model_name},
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    self.status = NodeStatus.ONLINE
                    self.last_seen = datetime.now()
                    return response.json()
                detail = None
                try:
                    detail = response.json().get("detail")
                except Exception:
                    pass
                if isinstance(detail, dict):
                    return {"success": False, "error": detail.get("message", "error"), **detail}
                return {
                    "success": False,
                    "error": detail or f"HTTP {response.status_code}",
                }
        except httpx.RequestError as e:
            self.status = NodeStatus.OFFLINE
            return {"success": False, "error": str(e)}

    def delete_model(self, model_name: str) -> dict | None:
        """Delete ``model_name`` from this node's config (ADR-008 §4.1)."""
        if self._is_self_loop():
            from llauncher import operations as ops

            self.status = NodeStatus.ONLINE
            self.last_seen = datetime.now()
            return ops.delete_model(model_name, caller="local").to_dict()
        try:
            with self._get_client() as client:
                response = client.delete(
                    f"{self.base_url}/models/{model_name}",
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    self.status = NodeStatus.ONLINE
                    self.last_seen = datetime.now()
                    return response.json()
                detail = None
                try:
                    detail = response.json().get("detail")
                except Exception:
                    pass
                if isinstance(detail, dict):
                    return {"success": False, "error": detail.get("message", "error"), **detail}
                return {
                    "success": False,
                    "error": detail or f"HTTP {response.status_code}",
                }
        except httpx.RequestError as e:
            self.status = NodeStatus.OFFLINE
            return {"success": False, "error": str(e)}

    def stop_server(self, port: int) -> dict | None:
        """Stop a server on this node.

        Args:
            port: Port of the server to stop.

        Returns:
            Result dictionary or None if failed.
        """
        if self._is_self_loop():
            from llauncher import operations as ops

            self.status = NodeStatus.ONLINE
            self.last_seen = datetime.now()
            return ops.stop(port, caller="local").to_dict()
        try:
            with self._get_client() as client:
                response = client.post(
                    f"{self.base_url}/stop/{port}",
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    self.status = NodeStatus.ONLINE
                    self.last_seen = datetime.now()
                    return response.json()
                elif response.status_code == 404:
                    return {"success": False, "error": f"No server running on port {port}"}
                return {"success": False, "error": f"HTTP {response.status_code}"}
        except httpx.RequestError as e:
            self.status = NodeStatus.OFFLINE
            return {"success": False, "error": str(e)}

    def get_logs(self, port: int, lines: int = 100) -> list[str] | None:
        """Get recent log lines for a server on this node.

        Args:
            port: Port of the server.
            lines: Number of lines to retrieve.

        Returns:
            List of log lines or None if failed.
        """
        try:
            with self._get_client() as client:
                response = client.get(
                    f"{self.base_url}/logs/{port}",
                    params={"lines": lines},
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    self.status = NodeStatus.ONLINE
                    self.last_seen = datetime.now()
                    data = response.json()
                    return data.get("lines", [])
                return None
        except httpx.RequestError:
            self.status = NodeStatus.OFFLINE
            return None

    def to_dict(self) -> dict:
        """Convert node info to dictionary."""
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "timeout": self.timeout,
            "has_api_key": self.api_key is not None,
            "status": self.status.value,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "error_message": self._error_message,
        }
