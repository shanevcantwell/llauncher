"""Remote node client for connecting to llauncher agents.

Per ADR-LLNCH-009 the topology is symmetric: every node runs an agent and
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
import socket
from datetime import datetime
from enum import Enum

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from llauncher.core.delegation import is_agent_process

logger = logging.getLogger(__name__)


class NodeConfig(BaseModel):
    """Validated connection parameters for a :class:`RemoteNode` (issue #27).

    Single source of truth for the shape a node's connection parameters
    must satisfy — host/port/timeout — so ``RemoteNode`` and
    ``NodeRegistry`` validate identically instead of each hand-rolling
    checks. The bug this closes: a host field with an embedded port
    (``"192.168.137.2:8765"``) plus a *separate* port field produced a
    malformed ``base_url`` (``http://192.168.137.2:8765:8765``) with no
    error until the request failed.
    """

    name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(default=8765, ge=1024, le=65535)
    timeout: float = Field(default=5.0, gt=0)

    @field_validator("host")
    @classmethod
    def _reject_embedded_port(cls, value: str) -> str:
        """Reject a host carrying a single embedded ``:port`` suffix.

        Exactly one colon is the ``host:port`` shape (the #27 bug);
        IPv6 literals (``::1``, ``2001:db8::1``) carry two-or-more and
        are left alone rather than mis-flagged.
        """
        if value.count(":") == 1:
            raise ValueError(
                f"host {value!r} must not embed a port — use the separate "
                "port field instead"
            )
        return value

    @property
    def base_url(self) -> str:
        """Base URL for this node's agent."""
        return f"http://{self.host}:{self.port}"


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
    """The agent port the local node is configured to bind.

    Sourced from :data:`llauncher.core.settings.AGENT_PORT` (issue #200,
    SP-2) rather than re-reading ``LLAUNCHER_AGENT_PORT`` inline, so the
    agent port has a single source of truth shared with the delegation
    gate. Read at call time (via attribute access on the settings module)
    so test patches / reloaded settings take effect.
    """
    from llauncher.core import settings

    return settings.AGENT_PORT


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
        try:
            config = NodeConfig(name=name, host=host, port=port, timeout=timeout)
        except ValidationError as e:
            # Collapse pydantic's multi-line error into the first,
            # most-relevant message so callers (registry, UI) can
            # surface a single readable line rather than a traceback.
            raise ValueError(e.errors()[0]["msg"]) from e
        self._config = config
        self.name = config.name
        self.host = config.host
        self.port = config.port
        self.timeout = config.timeout
        self.api_key: str | None = api_key if api_key else None
        self.status = NodeStatus.OFFLINE
        self.last_seen: datetime | None = None
        self._error_message: str | None = None

    @property
    def base_url(self) -> str:
        """Get the base URL for this node's agent."""
        return self._config.base_url

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

    def _targets_local_node(self) -> bool:
        """Return True if this node points at the *local* node/agent.

        The original (#62) self-loop predicate. Two independent signals:

        * ``self.name == "local"`` — the registry convention from
          :mod:`llauncher.remote.registry`.
        * ``self.host in {localhost, 127.0.0.1, ::1, 0.0.0.0, this
          machine's hostname}`` AND ``self.port`` matches the local
          agent's bind port.

        Conservative on purpose: over-reporting "local" would route
        genuinely-remote calls in-process and silently lose multi-node
        isolation, which is worse than one extra loopback HTTP call.
        """
        if self.name == "local":
            return True
        return (
            self.host in _local_host_names()
            and self.port == _local_agent_port()
        )

    def _is_self_loop(self) -> bool:
        """Return True only when the agent is calling its *own* local node.

        This is the #62 self-loop optimization, narrowed per issue #200.
        Pre-#200 the predicate was just :meth:`_targets_local_node` — which
        correctly captured the agent talking to itself but *also* captured
        operator front-ends (MCP, UI) pointing a ``RemoteNode`` at the
        local agent. Those front-ends are NOT the agent and, under the
        system-mode deployment (#194), must defer launches *to* the agent
        over HTTP rather than spawning in-process.

        The narrowing ANDs in
        :func:`llauncher.core.delegation.is_agent_process`: the short-
        circuit fires only when *this* process is the agent AND the target
        is the local node. So:

        * agent → local node  → in-process (the preserved #62 optimization);
        * agent → remote peer → HTTP (multi-node isolation intact);
        * front-end → anything → HTTP (where the delegation gate decides,
          before a ``RemoteNode`` is even built).
        """
        return is_agent_process() and self._targets_local_node()

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

        On the #62 self-loop (this process *is* the agent, target is the
        local node) the payload is pure local-state introspection, so we
        build it in-process via :func:`llauncher.core.node_info.get_node_info`
        rather than burning a loopback HTTP round-trip, the auth hop, and
        the FastAPI middleware chain to read data this process already has
        (issue #125). The endpoint handler sources the *same* builder, so
        the in-process and over-the-wire payloads cannot drift.

        Returns:
            Node info dictionary or None if unavailable.
        """
        if self._is_self_loop():
            from llauncher.core import node_info as _node_info

            self.status = NodeStatus.ONLINE
            self.last_seen = datetime.now()
            return _node_info.get_node_info()
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

    def get_model_validation(self, vram: bool = True) -> dict | None:
        """Read-only validation report for all models on this node (#475, ADR-LLNCH-027).

        Args:
            vram: When ``False``, ask the node to skip the advisory VRAM
                check (``?vram=false``) — no ``nvidia-smi`` shell-out on the
                peer. The UI tab passes ``False``; it re-renders on every
                widget interaction and never gates a badge on that verdict.

        Returns:
            A ``ValidationReport`` dict (``{checked_at, ok, models: [...]}``),
            or ``None`` if unavailable. Mirrors :meth:`get_models`'s shape of
            error handling — no auth/transport surprises for the UI tab.
        """
        if self._is_self_loop():
            from llauncher import operations as ops

            self.status = NodeStatus.ONLINE
            self.last_seen = datetime.now()
            return ops.validate_models(vram=vram).model_dump(mode="json")
        try:
            with self._get_client() as client:
                response = client.get(
                    f"{self.base_url}/models/validate",
                    params={"vram": str(bool(vram)).lower()},
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
        """Start ``model_name`` on ``port`` on this node (ADR-LLNCH-010).

        Per ADR-LLNCH-010, port is supplied at the call site. The HTTP body
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
        """Swap the model on ``port`` to ``model_name`` per ADR-LLNCH-011."""
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
        """Delete ``model_name`` from this node's config (ADR-LLNCH-008 §4.1)."""
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

        Issue #140 timeout contract: the agent's ``POST /stop/{port}``
        acknowledges a live process with **202** and
        ``action="stopping"`` and terminates it asynchronously, so this
        call returns well inside ``self.timeout`` even when the
        llama-server needs the full SIGTERM grace to unload. Callers
        treat ``stopping`` as accepted-success and observe completion
        through the next status refresh. The self-loop path keeps the
        *synchronous* ``ops.stop`` on purpose: there is no transport
        timeout in-process, the blocking call returns the definitive
        outcome, and a short-lived caller (CLI) must not exit while a
        background termination is still mid-grace.

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
                # 200 (already_empty / legacy stopped) and 202
                # (stopping, issue #140) are both success envelopes.
                if response.status_code in (200, 202):
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

    def read_audit(
        self,
        limit: int = 200,
        action_filter: str | None = None,
        result_filter: str | None = None,
    ) -> list[dict] | None:
        """Read recent audit-log entries from this node (issue #64).

        Args:
            limit: Maximum number of entries to read (bounded tail).
            action_filter: Optional ``AuditAction`` value to filter on.
            result_filter: Optional ``AuditResult`` value to filter on.

        Returns:
            A list of :meth:`AuditEntry.to_dict` dicts (chronological,
            newest last — matches ``core.audit_log.read_entries``). Returns
            ``None`` on transport or HTTP error so callers can distinguish
            "log empty" (``[]``) from "unreachable" (``None``).
        """
        if self._is_self_loop():
            from llauncher.core import audit_log

            self.status = NodeStatus.ONLINE
            self.last_seen = datetime.now()
            entries = audit_log.read_entries(limit=int(limit))
            if action_filter:
                entries = [e for e in entries if e.action.value == action_filter]
            if result_filter:
                entries = [e for e in entries if e.result.value == result_filter]
            return [e.to_dict() for e in entries]
        try:
            params: dict[str, str | int] = {"limit": int(limit)}
            if action_filter:
                params["action"] = action_filter
            if result_filter:
                params["result"] = result_filter
            with self._get_client() as client:
                response = client.get(
                    f"{self.base_url}/audit",
                    params=params,
                    headers=self._get_headers(),
                )
                if response.status_code == 200:
                    self.status = NodeStatus.ONLINE
                    self.last_seen = datetime.now()
                    data = response.json()
                    return data if isinstance(data, list) else []
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


def local_agent_node() -> RemoteNode:
    """Construct a ``RemoteNode`` aimed at the local agent (#200 delegation).

    Single construction point for the delegation target — host, port, and
    token live here rather than being copy-pasted into each front-end. The
    node is named ``"local"`` and carries the resolved ``X-Api-Key``;
    because a front-end is not the agent process, its :meth:`_is_self_loop`
    is False, so verb calls go over HTTP to ``127.0.0.1:AGENT_PORT``.

    This factory lives in the ``remote`` layer rather than
    :mod:`llauncher.core.delegation` (issue #200 follow-up): it constructs a
    ``RemoteNode``, so its natural home is alongside that class. ``core``
    owns only the *decision* (``should_delegate``); ``remote`` owns *building
    the target*, keeping ``core`` free of any edge to ``remote`` or
    ``agent``. ``settings``/``agent_token`` are imported at call time —
    downward (``remote → core``) edges, read late so test patches and
    reloaded settings take effect.
    """
    from llauncher.core import settings
    from llauncher.core.agent_token import resolve_agent_token

    token = resolve_agent_token(allow_generate=False)
    return RemoteNode("local", "127.0.0.1", port=settings.AGENT_PORT, api_key=token)
