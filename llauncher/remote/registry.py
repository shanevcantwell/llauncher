"""Node registry for managing remote llauncher agents."""

import json
import logging
import os
from typing import Iterator

from llauncher.core.settings import LAUNCHER_STATE_DIR
from llauncher.remote.node import RemoteNode, NodeStatus

# Derived from the single LAUNCHER_STATE_DIR base (issue #196). With
# LAUNCHER_STATE_DIR unset these resolve to ~/.llauncher/* exactly as
# before.
NODES_FILE = LAUNCHER_STATE_DIR / "nodes.json"
# Sibling secrets file for remote-node API tokens (issue #132). Kept
# separate from ``nodes.json`` so the C10 invariant — "credentials never
# live in the registry file" — stays crisp. Different secret classes
# (per-node tokens, future TLS keys per #86) intentionally live in their
# own files: blast-radius per-concern, corruption degrades gracefully,
# and the filename itself telegraphs "this is the credential, treat
# accordingly". Do NOT ratchet tokens back into nodes.json.
NODE_TOKENS_FILE = LAUNCHER_STATE_DIR / "node_tokens.json"
logger = logging.getLogger(__name__)


class NodeRegistry:
    """Manages a collection of remote nodes.

    Persists node configurations to ``nodes.json`` under
    ``LAUNCHER_STATE_DIR`` (default ``~/.llauncher``; see issue #196).
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
            self._populate_local_token()
            return

        migrated = False
        try:
            # utf-8-sig: PARSE-AT-THE-DOOR tolerance for a hand-edited file
            # picking up a BOM from a Windows editor (issue #310, same class
            # as agent.env's BOM/CRLF hardening); a strict superset of utf-8
            # for BOM-less input so this is a no-op in the common case.
            data = json.loads(NODES_FILE.read_text(encoding="utf-8-sig"))
            for name, node_data in data.items():
                try:
                    # Backward compat: old files use "api_key", new files use "has_api_key"
                    raw_key = node_data.get("api_key")
                    host = node_data["host"]
                    port = node_data.get("port", 8765)
                    if isinstance(host, str) and host.count(":") == 1:
                        # Issue #27: a prior UI bug let an embedded port slip
                        # into the host field (e.g. "192.168.137.2:8765") on
                        # top of a separate port field, producing a malformed
                        # base_url. Migrate once, at the door (PARSE-AT-THE-DOOR):
                        # split the embedded port out and prefer it as the
                        # real port, then persist the corrected shape below.
                        fixed_host, _, port_str = host.rpartition(":")
                        if port_str.isdigit():
                            port = int(port_str)
                        logger.warning(
                            f"Migrated corrupted host for node '{name}': "
                            f"{host!r} -> host={fixed_host!r}, port={port}"
                        )
                        host = fixed_host
                        migrated = True
                    self._nodes[name] = RemoteNode(
                        name=node_data["name"],
                        host=host,
                        port=port,
                        timeout=node_data.get("timeout", 5.0),
                        api_key=raw_key,
                    )
                except (KeyError, ValueError) as e:
                    # Issue #273: a single entry that fails NodeConfig
                    # validation (or is missing a required key) after the
                    # embedded-port migration above must not take the
                    # whole pass down with it. Scope the failure to this
                    # entry — log and drop only ``name``, keep the rest.
                    logger.warning(
                        f"Skipping node '{name}' in {NODES_FILE}: {e}"
                    )
                    continue
        except (json.JSONDecodeError, KeyError, ValueError):
            # Whole-file corruption (bad JSON, or a non-dict top-level
            # shape whose per-entry iteration itself raises before the
            # inner try can scope it) — start fresh rather than crash UI
            # startup.
            self._nodes.clear()
            migrated = False

        self._populate_local_token()
        self._populate_remote_tokens()

        if migrated:
            # Persist the corrected shape once so the migration doesn't
            # re-run (and re-log) on every subsequent load.
            self._save()

    def _resolve_local_token(self) -> str | None:
        """Resolve the local agent's auth token, or return ``None``.

        The UI process is separate from the agent process (Streamlit vs.
        ``llauncher-agent`` under systemd / NSSM / foreground), so it
        does *not* inherit ``LLAUNCHER_AGENT_TOKEN`` from the agent's
        environment. We source via
        :func:`llauncher.core.agent_token.resolve_agent_token` with
        ``allow_generate=False`` — that reads the env var first, then
        parses the on-disk ``~/.llauncher/agent.env`` directly (issue
        #284 — single live source, no separate token-mirror file).
        ``allow_generate=False`` because only the agent itself should
        ever materialize a fresh token; the UI must be a pure consumer.

        The token resolver lives in :mod:`llauncher.core.agent_token`
        (issue #171) precisely so ``remote`` can read it without
        importing ``agent.*`` — the layering violation this edge used to
        embody.

        Returns ``None`` if no token can be resolved; callers should
        leave ``api_key=None`` in that case, which matches the
        pre-token behavior for unauthenticated agents (loopback,
        token-less first run).
        """
        try:
            from llauncher.core.agent_token import resolve_agent_token
            return resolve_agent_token(allow_generate=False)
        except Exception:
            # Token resolution must never break registry load. If the
            # helper fails for any reason (filesystem error, import
            # cycle in a constrained test env, etc.), fall through to
            # the unauthenticated case rather than propagating.
            return None

    def _load_node_tokens(self) -> dict[str, str]:
        """Read ``~/.llauncher/node_tokens.json`` into a ``{name: token}`` dict.

        Returns ``{}`` on missing-or-corrupt; never raises. Token
        resolution must not break registry load (the registry is on the
        critical path for UI startup).
        """
        if not NODE_TOKENS_FILE.exists():
            return {}
        try:
            # utf-8-sig: see the matching NODES_FILE read above (#310) --
            # node_tokens.json carries credentials, so BOM-tolerance here
            # is the same door-normalization discipline as agent.env.
            data = json.loads(NODE_TOKENS_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                # Defensive: filter out non-string values; an attacker
                # who can write the file shouldn't be able to inject a
                # non-string into an api_key slot, but cheap to guard.
                return {k: v for k, v in data.items() if isinstance(v, str)}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not read {NODE_TOKENS_FILE}: {e}")
        return {}

    def _populate_remote_tokens(self) -> None:
        """Stamp resolved remote-node tokens onto loaded ``RemoteNode`` instances.

        The persisted ``nodes.json`` deliberately does NOT carry api_key
        (security control C10 / #83). The sibling ``node_tokens.json``
        carries the operator-supplied tokens. On load, walk it and stamp
        each match onto the corresponding ``RemoteNode.api_key``.

        Missing entries leave ``api_key=None`` — we do NOT synthesize
        from ``agent.env`` (which carries the local agent's token);
        cross-pollinating a local token onto a remote node would be a
        credential-confusion bug, symmetric to
        :meth:`_populate_local_token`'s "only touches ``local``" guard.

        The ``local`` entry is also skipped here even if it happens to
        appear in ``node_tokens.json``: its canonical source is
        ``agent.env`` via ``_populate_local_token``, and trusting
        ``node_tokens.json`` for ``local`` would create a drift
        opportunity.
        """
        tokens = self._load_node_tokens()
        for name, token in tokens.items():
            if name == "local":  # pragma: no cover - C10 security guard: 'local' token is never sourced from the sidecar (canonical source is agent.env); skip to prevent credential confusion
                continue
            node = self._nodes.get(name)
            if node is not None and not node.api_key:
                node.api_key = token

    def _save_node_tokens(self) -> None:
        """Write the remote-node tokens sidecar file.

        Full rewrite each call: any node that was removed from
        ``self._nodes`` or whose ``api_key`` was cleared automatically
        falls out of the file. The ``local`` entry is excluded
        unconditionally — its token lives in ``agent.env``;
        duplicating it here would create drift.

        Mode 0600 on the file, 0700 on the parent dir — matching the
        ``nodes.json`` and ``agent.env`` conventions.
        """
        data = {
            name: node.api_key
            for name, node in self._nodes.items()
            if name != "local" and node.api_key
        }

        NODE_TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            NODE_TOKENS_FILE.parent.chmod(0o700)
        except OSError:
            # Best-effort; chmod is a no-op on some filesystems (e.g.
            # WSL-on-NTFS). The 0600 on the file itself is the
            # load-bearing protection.
            pass

        if not data:
            # No remote tokens to persist. If a file exists from a
            # previous save, rewrite it to an empty object so removed
            # tokens don't linger on disk.
            if NODE_TOKENS_FILE.exists():
                NODE_TOKENS_FILE.write_text("{}")
                try:
                    os.chmod(NODE_TOKENS_FILE, 0o600)
                except OSError as e:
                    logger.warning(
                        f"Could not set restrictive permissions on {NODE_TOKENS_FILE}: {e}"
                    )
            return

        NODE_TOKENS_FILE.write_text(json.dumps(data, indent=2))
        try:
            os.chmod(NODE_TOKENS_FILE, 0o600)
        except OSError as e:
            logger.warning(
                f"Could not set restrictive permissions on {NODE_TOKENS_FILE}: {e}"
            )

    def _populate_local_token(self) -> None:
        """Self-heal: stamp the resolved token onto the ``local`` node.

        The persisted ``nodes.json`` deliberately does not store
        ``api_key`` (security control C10 / issue #83). So every
        ``_load`` re-instantiates the ``local`` entry with
        ``api_key=None``. Without this self-heal, the UI would send
        no ``X-Api-Key`` header to the local agent and bounce off
        every non-exempt endpoint with 401 — see issue #126 for the
        ADR-LLNCH-003 exempt-paths drift that makes this acute.

        Only the entry literally named ``local`` is touched — remote
        nodes use their own tokens (operator-supplied via the Nodes
        tab) which the UI cannot derive from agent.auth.
        """
        local = self._nodes.get("local")
        if local is None or local.api_key is not None:
            return
        token = self._resolve_local_token()
        if token:
            local.api_key = token

    def _save(self) -> None:
        """Save nodes to the persistent file.

        Security control C10 (security-hardening-plan §3): the registry
        file may contain operator-visible node metadata. We ``chmod 0600``
        on every save (not just on creation) so a file that already
        existed at a wider mode — e.g. from a pre-#83 build that inherited
        umask — gets defensively re-tightened. Parent dir is also pinned
        to ``0700`` to match ``llauncher/agent/auth.py``'s convention for
        ``~/.llauncher/``.
        """
        NODES_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            NODES_FILE.parent.chmod(0o700)
        except OSError:  # pragma: no cover - best-effort dir chmod; no-op/failure on exotic FS (WSL-on-NTFS, some cloud mounts) is swallowed, the 0600 on the file is the load-bearing protection
            # Best-effort; chmod is a no-op on some filesystems (e.g.
            # WSL-on-NTFS). The 0600 on the file itself is the
            # load-bearing protection.
            pass

        data = {}
        for name, node in self._nodes.items():
            data[name] = {
                "name": node.name,
                "host": node.host,
                "port": node.port,
                "timeout": node.timeout,
                "has_api_key": node.api_key is not None,
            }

        NODES_FILE.write_text(json.dumps(data, indent=2))

        try:
            os.chmod(NODES_FILE, 0o600)
        except OSError as e:  # pragma: no cover - best-effort re-tighten; chmod failure on exotic FS (WSL-on-NTFS, cloud mounts) is logged-and-swallowed, not fatal
            logger.warning(f"Could not set restrictive permissions on {NODES_FILE}: {e}")

        # Sidecar tokens file (issue #132). Wrapped so a token-write
        # failure does not corrupt the nodes.json write that just
        # succeeded — the two files are independent on purpose.
        try:
            self._save_node_tokens()
        except Exception as e:  # noqa: BLE001 — defensive isolation  # pragma: no cover - isolates a sidecar token-write failure from the just-succeeded nodes.json write (#132); the two files are independent on purpose
            logger.warning(f"Could not write {NODE_TOKENS_FILE}: {e}")

    def add_node(
        self,
        name: str,
        host: str,
        port: int = 8765,
        timeout: float = 5.0,
        api_key: str | None = None,
        overwrite: bool = False,
    ) -> tuple[bool, str]:
        """Add a node to the registry.

        Args:
            name: Unique name for this node.
            host: Hostname or IP address.
            port: Agent port.
            timeout: Connection timeout in seconds.
            api_key: Optional API key for authenticated requests to this node.
            overwrite: If True, overwrite existing node with same name.

        Returns:
            Tuple of (success, message).
        """
        if name in self._nodes and not overwrite:
            return False, f"Node '{name}' already exists. Use overwrite=True to replace."

        try:
            node = RemoteNode(
                name=name,
                host=host,
                port=port,
                timeout=timeout,
                api_key=api_key,
            )
        except ValueError as e:
            # NodeConfig validation failure (issue #27) — surface as a
            # normal (success, message) failure rather than an
            # exception, matching the rest of this method's contract.
            return False, str(e)

        self._nodes[name] = node
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

    def is_local_agent_ready(self) -> bool:
        """Check if the local agent is ready.

        Returns:
            True if agent is responding, False otherwise.
        """
        import os
        import socket

        AGENT_PORT = int(os.getenv("LLAUNCHER_AGENT_PORT", "8765"))

        # Check if local node exists and is online
        local_node = self.get_node("local")
        if local_node and local_node.ping():
            return True

        # Check if port is in use
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            try:
                s.connect(("127.0.0.1", AGENT_PORT))
                # Something is running - add to registry if not present.
                # Source the token so the synthesized entry can
                # authenticate; falls back to None for unauth'd loopback
                # agents (matches pre-token-resolver behavior).
                if not local_node:
                    token = self._resolve_local_token()
                    self.add_node(
                        "local", "localhost", AGENT_PORT,
                        api_key=token, overwrite=True,
                    )
                return True
            except (ConnectionRefusedError, TimeoutError, OSError):
                pass

        return False

    # ``start_local_agent`` removed in M4 Slice 12 (issue #49 / audit H2).
    # ADR-LLNCH-009 prescribes a symmetric hub-spoke topology where every node —
    # including ``local`` — is started deliberately by the user (typically
    # via ``llauncher-agent``), not auto-spawned by whatever tool happened
    # to load first. The UI now renders an "agent down" banner via
    # :func:`llauncher.ui.app.show_agent_down_banner` when the local agent
    # is unreachable.

    def to_dict(self) -> dict:
        """Convert registry to dictionary representation."""
        return {
            name: node.to_dict()
            for name, node in self._nodes.items()
        }
