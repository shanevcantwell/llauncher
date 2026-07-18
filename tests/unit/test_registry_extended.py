"""Extended tests for the NodeRegistry module."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llauncher.remote.node import RemoteNode, NodeStatus
from llauncher.remote.registry import NodeRegistry, NODES_FILE


class TestIsLocalAgentReady:
    """Tests for NodeRegistry.is_local_agent_ready method."""

    def test_is_local_agent_ready_with_existing_online_node(self, monkeypatch):
        """Test when local node exists and is online."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()

        # Add a local node
        registry.add_node("local", "localhost", 8765)
        local_node = registry.get_node("local")
        assert local_node is not None
        assert local_node.status == NodeStatus.OFFLINE

        # Mock get_node to return the local node with ONLINE status
        original_get_node = registry.get_node

        def mock_get_node(name):
            node = original_get_node(name)
            if node:
                node.status = NodeStatus.ONLINE
            return node

        monkeypatch.setattr(registry, "get_node", mock_get_node)

        # Mock ping to return True
        monkeypatch.setattr(RemoteNode, "ping", lambda self: True)

        # Also mock socket to avoid actual connection attempts
        def mock_socket_class(family, type_):
            s = MagicMock()
            s.connect = MagicMock(side_effect=ConnectionRefusedError("Not actually connecting"))
            return s

        monkeypatch.setattr("socket.socket", mock_socket_class)

        result = registry.is_local_agent_ready()

        # Should return True when node exists and ping succeeds
        assert result is True

    def test_is_local_agent_ready_socket_success(self, monkeypatch):
        """Test when socket connection succeeds but node doesn't exist in registry."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()

        # Mock socket to simulate successful connection
        mock_socket = MagicMock()
        mock_socket.connect.return_value = None

        def mock_socket_class(family, type_):
            return mock_socket

        monkeypatch.setattr("socket.socket", mock_socket_class)

        # Mock add_node to track calls. Accepts api_key as a kwarg
        # because is_local_agent_ready() now sources a token via
        # _resolve_local_token() when synthesizing the local entry
        # (issue #125).
        added = []

        def mock_add_node(name, host, port, api_key=None, overwrite=False):
            added.append((name, host, port, api_key))
            return True, "Added"

        monkeypatch.setattr(registry, "add_node", mock_add_node)
        # No token file in the test env → resolver returns None → the
        # synthesized entry has api_key=None, matching pre-#125 behavior.
        monkeypatch.setattr(registry, "_resolve_local_token", lambda: None)

        # Mock get_node to return None (node not in registry yet)
        def mock_get_node(name):
            return None

        monkeypatch.setattr(registry, "get_node", mock_get_node)

        # Mock ping to return True
        monkeypatch.setattr(RemoteNode, "ping", lambda self: True)

        result = registry.is_local_agent_ready()

        # Should return True when socket connects successfully
        assert result is True
        assert added == [("local", "127.0.0.1", 8765, None)]

    def test_load_migrates_localhost_local_node(self, tmp_path, monkeypatch):
        """The persisted local target is normalized to IPv4 exactly once."""
        nodes_file = tmp_path / "nodes.json"
        nodes_file.write_text(json.dumps({
            "local": {
                "name": "local", "host": "localhost",
                "port": 8765, "timeout": 5.0, "has_api_key": False,
            },
            "remote": {
                "name": "remote", "host": "localhost",
                "port": 8766, "timeout": 5.0, "has_api_key": False,
            },
        }))
        monkeypatch.setattr("llauncher.remote.registry.NODES_FILE", nodes_file)

        registry = NodeRegistry()

        local = registry.get_node("local")
        remote = registry.get_node("remote")
        assert local is not None
        assert remote is not None
        assert local.host == "127.0.0.1"
        assert remote.host == "localhost"
        persisted = json.loads(nodes_file.read_text())
        assert persisted["local"]["host"] == "127.0.0.1"

    def test_is_local_agent_ready_socket_failure(self, monkeypatch):
        """Test when socket connection fails."""
        from llauncher.remote.registry import NodeRegistry
        from unittest.mock import MagicMock

        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()

        # Mock get_node to return None (no local node in registry)
        def mock_get_node(name):
            return None

        monkeypatch.setattr(registry, "get_node", mock_get_node)

        # Mock ping to return False (local node not responding)
        def mock_ping(self):
            return False

        monkeypatch.setattr(RemoteNode, "ping", mock_ping)

        # Mock socket.socket to return a mock socket whose connect raises exception
        def mock_socket_constructor(family, type_, proto=0):
            # Make __enter__ return self so that the with statement works correctly
            mock_socket = MagicMock()
            mock_socket.__enter__.return_value = mock_socket
            def mock_connect(*args, **kwargs):
                raise ConnectionRefusedError("Connection refused")
            mock_socket.connect = mock_connect
            def mock_settimeout(timeout):
                pass
            mock_socket.settimeout = mock_settimeout
            return mock_socket

        # Patch socket.socket at the module level where it's used
        monkeypatch.setattr("socket.socket", mock_socket_constructor)

        result = registry.is_local_agent_ready()

        # Should return False when socket fails
        assert result is False

    def test_is_local_agent_ready_os_error(self, monkeypatch):
        """Test when socket connection raises OSError."""
        from llauncher.remote.registry import NodeRegistry
        from unittest.mock import MagicMock

        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()

        # Mock get_node to return None (no local node in registry)
        def mock_get_node(name):
            return None

        monkeypatch.setattr(registry, "get_node", mock_get_node)

        # Mock ping to return False (local node not responding)
        monkeypatch.setattr(RemoteNode, "ping", lambda self: False)

        # Mock socket.socket to return a mock socket whose connect raises exception
        def mock_socket_constructor(family, type_, proto=0):
            # Make __enter__ return self so that the with statement works correctly
            mock_socket = MagicMock()
            mock_socket.__enter__.return_value = mock_socket
            def mock_connect(*args, **kwargs):
                raise OSError("Network error")
            mock_socket.connect = mock_connect
            def mock_settimeout(timeout):
                pass
            mock_socket.settimeout = mock_settimeout
            return mock_socket

        # Patch socket.socket at the module level where it's used
        monkeypatch.setattr("socket.socket", mock_socket_constructor)

        result = registry.is_local_agent_ready()

        # Should return False when socket raises OSError
        assert result is False


class TestStartLocalAgentRemoved:
    """Regression guard for issue #49 / audit H2.

    ``NodeRegistry.start_local_agent`` was deleted in M4 Slice 12. ADR-009's
    symmetric topology says the user starts the agent via the CLI; the
    UI is purely a viewer. This test exists so a future revert that
    re-introduces the auto-spawn surface fails loudly.
    """

    def test_start_local_agent_method_is_gone(self) -> None:
        """The method must not be re-introduced."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()
        assert not hasattr(registry, "start_local_agent"), (
            "NodeRegistry.start_local_agent was removed in M4 Slice 12 "
            "(issue #49). Re-introducing it conflicts with ADR-009 "
            "(symmetric hub-spoke topology). The user starts the agent "
            "with `llauncher-agent`; the UI does not auto-spawn."
        )


class TestGetNodeInfoAll:
    """Tests for NodeRegistry.get_node_info_all method."""

    def test_get_node_info_all_mixed(self, monkeypatch):
        """Test when some nodes succeed and some fail."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()
        registry.add_node("online-node", "localhost", 8765)
        registry.add_node("offline-node", "localhost", 8766)

        # Mock node info for online node
        def mock_get_node_info(self):
            if self.name == "online-node":
                return {"node_name": self.name, "status": "online"}
            return None  # Offline node returns None

        monkeypatch.setattr(RemoteNode, "get_node_info", mock_get_node_info)

        result = registry.get_node_info_all()

        # Should only include online node
        assert "online-node" in result
        assert "offline-node" not in result

    def test_get_node_info_all_success(self, monkeypatch):
        """Test when all nodes return info."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()
        registry.add_node("node1", "localhost", 8765)
        registry.add_node("node2", "localhost", 8766)

        # Mock get_node_info to return info for all nodes
        def mock_get_node_info(self):
            return {"node_name": self.name, "status": "online"}

        monkeypatch.setattr(RemoteNode, "get_node_info", mock_get_node_info)

        result = registry.get_node_info_all()

        assert len(result) == 2
        assert "node1" in result
        assert "node2" in result


class TestRefreshAll:
    """Tests for NodeRegistry.refresh_all method."""

    def test_refresh_all_results(self, monkeypatch):
        """Test that refresh_all returns status dictionary."""
        from llauncher.remote.registry import NodeRegistry

        registry = NodeRegistry()
        # Clear existing nodes to start with a clean slate for this test
        registry._nodes.clear()
        registry.add_node("node1", "localhost", 8765)
        registry.add_node("node2", "localhost", 8766)

        # Mock ping to return success and set status
        def mock_ping(self):
            self.status = NodeStatus.ONLINE
            return True

        monkeypatch.setattr(RemoteNode, "ping", mock_ping)

        results = registry.refresh_all()

        assert len(results) == 2
        assert "node1" in results
        assert "node2" in results
        assert results["node1"] == NodeStatus.ONLINE
        assert results["node2"] == NodeStatus.ONLINE


class TestToDict:
    """Tests for NodeRegistry.to_dict method."""

    def test_to_dict_conversion(self):
        """Test conversion to dictionary."""
        registry = NodeRegistry()
        registry.add_node("node1", "localhost", 8765)

        data = registry.to_dict()

        assert "node1" in data
        assert data["node1"]["name"] == "node1"
        assert data["node1"]["host"] == "localhost"
        assert data["node1"]["port"] == 8765


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission semantics required"
)
class TestRegistryFilePermissions:
    """Regression tests for security control C10 / assertion C10-a.

    The registry file at ``~/.llauncher/nodes.json`` may contain operator-
    visible node metadata (host, port, has_api_key flag). It must be
    created and maintained at mode ``0600`` (owner-only read/write).

    Skipped on Windows: ``os.chmod`` is a near no-op there and
    ``st_mode & 0o777`` does not reliably return POSIX permission bits,
    so these assertions cannot be evaluated meaningfully (issue #106).

    References:
      - Issue #83
      - docs/plans/security-hardening-plan.md §3 C10, §4 C10-a, §5.6
    """

    def test_save_creates_file_at_mode_0600(self, tmp_path, monkeypatch):
        """C10-a: After ``node add``, the registry file has mode ``0600``.

        Covers the *creation* path: file does not exist when add_node runs.
        """
        from llauncher.remote import registry as registry_mod

        nodes_file = tmp_path / ".llauncher" / "nodes.json"
        monkeypatch.setattr(registry_mod, "NODES_FILE", nodes_file)

        reg = registry_mod.NodeRegistry()
        reg._nodes.clear()
        ok, _ = reg.add_node("alpha", "10.0.0.1", 8765)

        assert ok
        assert nodes_file.exists()
        mode = os.stat(nodes_file).st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got 0o{mode:o}"

    def test_save_retightens_widened_existing_file(self, tmp_path, monkeypatch):
        """Defensive: an existing world-readable file (e.g. from a
        pre-#83 build inheriting umask) must be re-tightened on the next
        save, not left as-is. This is the "subsequent writes" half of
        the issue spec.
        """
        from llauncher.remote import registry as registry_mod

        nodes_file = tmp_path / ".llauncher" / "nodes.json"
        nodes_file.parent.mkdir(parents=True, exist_ok=True)
        nodes_file.write_text("{}")
        os.chmod(nodes_file, 0o644)  # simulate pre-#83 wider mode
        assert (os.stat(nodes_file).st_mode & 0o777) == 0o644

        monkeypatch.setattr(registry_mod, "NODES_FILE", nodes_file)

        reg = registry_mod.NodeRegistry()
        reg._nodes.clear()
        reg.add_node("beta", "10.0.0.2", 8765)

        mode = os.stat(nodes_file).st_mode & 0o777
        assert mode == 0o600, (
            f"registry file was not re-tightened on save: 0o{mode:o}"
        )

    def test_save_tightens_parent_directory(self, tmp_path, monkeypatch):
        """Parent ``~/.llauncher/`` should be ``0700`` for symmetry with
        ``llauncher/agent/auth.py``. Best-effort: we assert the call was
        made by checking the resulting mode on a filesystem that supports
        chmod (POSIX tmp_path always does).
        """
        from llauncher.remote import registry as registry_mod

        nodes_file = tmp_path / ".llauncher" / "nodes.json"
        monkeypatch.setattr(registry_mod, "NODES_FILE", nodes_file)

        reg = registry_mod.NodeRegistry()
        reg._nodes.clear()
        reg.add_node("gamma", "10.0.0.3", 8765)

        mode = os.stat(nodes_file.parent).st_mode & 0o777
        assert mode == 0o700, f"expected parent 0700, got 0o{mode:o}"

    def test_remove_node_keeps_file_at_mode_0600(self, tmp_path, monkeypatch):
        """``remove_node`` also calls ``_save``; the file must stay 0600
        after removal, not just after the initial add.
        """
        from llauncher.remote import registry as registry_mod

        nodes_file = tmp_path / ".llauncher" / "nodes.json"
        monkeypatch.setattr(registry_mod, "NODES_FILE", nodes_file)

        reg = registry_mod.NodeRegistry()
        reg._nodes.clear()
        reg.add_node("delta", "10.0.0.4", 8765)
        # Tamper with mode after add to prove remove_node re-tightens.
        os.chmod(nodes_file, 0o644)

        reg.remove_node("delta")

        mode = os.stat(nodes_file).st_mode & 0o777
        assert mode == 0o600, f"expected 0600 after remove, got 0o{mode:o}"


class TestGetOnlineNodes:
    """Tests for NodeRegistry.get_online_nodes method."""

    def test_get_online_nodes(self):
        """Test getting online nodes."""
        registry = NodeRegistry()
        registry.add_node("online1", "localhost", 8765)
        registry.add_node("offline1", "localhost", 8766)
        registry.add_node("online2", "localhost", 8767)

        # Set status manually
        registry.get_node("online1").status = NodeStatus.ONLINE
        registry.get_node("offline1").status = NodeStatus.OFFLINE
        registry.get_node("online2").status = NodeStatus.ONLINE

        online_nodes = registry.get_online_nodes()

        assert len(online_nodes) == 2
        assert all(node.status == NodeStatus.ONLINE for node in online_nodes)
        assert "online1" in [node.name for node in online_nodes]
        assert "online2" in [node.name for node in online_nodes]
        assert "offline1" not in [node.name for node in online_nodes]


class TestLocalNodeTokenResolution:
    """Tests for the issue #125 fix: local node auto-sources auth token.

    The persisted ``nodes.json`` deliberately does not store ``api_key``
    (security control C10 / #83). Without the self-heal in ``_load``,
    the ``local`` entry would always reload with ``api_key=None`` and
    the UI would 401 on every non-exempt endpoint.
    """

    def test_load_self_heals_local_node_api_key_from_token_file(self, tmp_path, monkeypatch):
        """_load() stamps the resolved token onto a loaded ``local`` entry."""
        # Persist a nodes.json with a local entry, has_api_key=False
        # (the post-#83 shape; api_key is intentionally not on disk).
        nodes_file = tmp_path / "nodes.json"
        nodes_file.write_text(json.dumps({
            "local": {
                "name": "local", "host": "localhost",
                "port": 8765, "timeout": 5.0, "has_api_key": False,
            }
        }))
        monkeypatch.setattr("llauncher.remote.registry.NODES_FILE", nodes_file)
        # agent.env the resolver will parse.
        env_path = tmp_path / "agent.env"
        env_path.write_text("LLAUNCHER_AGENT_TOKEN=test-token-abc123\n")
        monkeypatch.setattr(
            "llauncher.core.agent_token.default_env_path", lambda: env_path
        )
        monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

        registry = NodeRegistry()
        local = registry.get_node("local")

        assert local is not None
        assert local.api_key == "test-token-abc123"

    def test_load_leaves_local_api_key_none_when_no_token(self, tmp_path, monkeypatch):
        """Self-heal is opt-in: no token available → api_key stays None."""
        nodes_file = tmp_path / "nodes.json"
        nodes_file.write_text(json.dumps({
            "local": {
                "name": "local", "host": "localhost",
                "port": 8765, "timeout": 5.0, "has_api_key": False,
            }
        }))
        monkeypatch.setattr("llauncher.remote.registry.NODES_FILE", nodes_file)
        # No agent.env, no env var → resolver returns None.
        env_path = tmp_path / "nonexistent.env"
        monkeypatch.setattr(
            "llauncher.core.agent_token.default_env_path", lambda: env_path
        )
        monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

        registry = NodeRegistry()
        local = registry.get_node("local")

        assert local is not None
        assert local.api_key is None

    def test_load_does_not_touch_remote_node_api_keys(self, tmp_path, monkeypatch):
        """Only the entry literally named ``local`` is self-healed.

        Remote nodes carry operator-supplied tokens the UI cannot
        derive from ``agent.auth``; self-healing them with the *local*
        agent's token would be a credential-confusion bug.
        """
        nodes_file = tmp_path / "nodes.json"
        nodes_file.write_text(json.dumps({
            "local": {
                "name": "local", "host": "localhost",
                "port": 8765, "timeout": 5.0, "has_api_key": False,
            },
            "remote-1": {
                "name": "remote-1", "host": "192.168.1.50",
                "port": 8765, "timeout": 5.0, "has_api_key": False,
            },
        }))
        monkeypatch.setattr("llauncher.remote.registry.NODES_FILE", nodes_file)
        env_path = tmp_path / "agent.env"
        env_path.write_text("LLAUNCHER_AGENT_TOKEN=local-token-only\n")
        monkeypatch.setattr(
            "llauncher.core.agent_token.default_env_path", lambda: env_path
        )
        monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)

        registry = NodeRegistry()

        assert registry.get_node("local").api_key == "local-token-only"
        # Remote node MUST NOT receive the local token.
        assert registry.get_node("remote-1").api_key is None

    def test_resolver_swallows_exceptions(self, tmp_path, monkeypatch):
        """If resolve_agent_token raises, _resolve_local_token returns None.

        Token resolution must never break registry load — the registry
        is on the critical path for the UI process startup.
        """
        nodes_file = tmp_path / "nodes.json"
        # Empty registry — exercises the no-local-entry path too.
        monkeypatch.setattr("llauncher.remote.registry.NODES_FILE", nodes_file)

        def boom(**kwargs):
            raise RuntimeError("filesystem on fire")

        monkeypatch.setattr("llauncher.core.agent_token.resolve_agent_token", boom)

        registry = NodeRegistry()
        # No exception bubbles up; resolver returns None defensively.
        assert registry._resolve_local_token() is None


class TestRemoteNodeTokenPersistence:
    """Tests for the issue #132 fix: remote node tokens survive UI restart.

    The persisted ``nodes.json`` deliberately does NOT carry api_key
    (security control C10 / #83). The sibling ``~/.llauncher/node_tokens.json``
    carries the operator-supplied tokens. These tests cover the
    load/save round-trip, the C10 file-boundary invariant, and the
    no-credential-confusion guard between local and remote token paths.
    """

    @staticmethod
    def _patch_paths(monkeypatch, tmp_path):
        """Common monkeypatch fixture: point both files at tmp_path."""
        from llauncher.remote import registry as registry_mod

        nodes_file = tmp_path / "nodes.json"
        tokens_file = tmp_path / "node_tokens.json"
        monkeypatch.setattr(registry_mod, "NODES_FILE", nodes_file)
        monkeypatch.setattr(registry_mod, "NODE_TOKENS_FILE", tokens_file)
        # Local-token resolver: no env, no on-disk agent.env in tmp.
        # Prevents the resolver from picking up the real user's token.
        agent_env_path = tmp_path / "agent.env-absent"
        monkeypatch.setattr(
            "llauncher.core.agent_token.default_env_path", lambda: agent_env_path
        )
        monkeypatch.delenv("LLAUNCHER_AGENT_TOKEN", raising=False)
        return nodes_file, tokens_file

    def test_round_trip_remote_token_persists_across_reload(self, tmp_path, monkeypatch):
        """add_node(api_key=...) → new NodeRegistry → token still on the node."""
        nodes_file, tokens_file = self._patch_paths(monkeypatch, tmp_path)

        reg = NodeRegistry()
        reg._nodes.clear()
        ok, _ = reg.add_node("remote-a", "192.168.1.50", 8765, api_key="tok-A")
        assert ok

        # Fresh registry — simulates UI restart.
        reg2 = NodeRegistry()
        node = reg2.get_node("remote-a")

        assert node is not None
        assert node.api_key == "tok-A"

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX permission semantics required"
    )
    def test_node_tokens_file_is_mode_0600(self, tmp_path, monkeypatch):
        """The sidecar file is created at 0600 (C10-parity for the secret file)."""
        _, tokens_file = self._patch_paths(monkeypatch, tmp_path)

        reg = NodeRegistry()
        reg._nodes.clear()
        reg.add_node("remote-b", "192.168.1.51", 8765, api_key="tok-B")

        assert tokens_file.exists()
        mode = os.stat(tokens_file).st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got 0o{mode:o}"

    def test_node_tokens_file_contains_only_tokens(self, tmp_path, monkeypatch):
        """The sidecar file is {name: token} only — no name/host/port leaks.

        Positive assertion on the C10 file boundary: the secret file
        carries strictly the secret, not a duplicate of the registry
        metadata. A future reader tempted to "consolidate" must see that
        this is by design.
        """
        _, tokens_file = self._patch_paths(monkeypatch, tmp_path)

        reg = NodeRegistry()
        reg._nodes.clear()
        reg.add_node("remote-c", "192.168.1.52", 8765, api_key="tok-C")

        payload = json.loads(tokens_file.read_text())
        assert payload == {"remote-c": "tok-C"}

    def test_nodes_json_never_contains_api_key(self, tmp_path, monkeypatch):
        """Regression guard: even with api_key supplied, nodes.json
        retains the post-#83 ``has_api_key`` discipline and never
        materializes the literal token.
        """
        nodes_file, _ = self._patch_paths(monkeypatch, tmp_path)

        reg = NodeRegistry()
        reg._nodes.clear()
        reg.add_node("remote-d", "192.168.1.53", 8765, api_key="tok-D")

        on_disk = json.loads(nodes_file.read_text())
        assert "remote-d" in on_disk
        assert on_disk["remote-d"].get("has_api_key") is True
        assert "api_key" not in on_disk["remote-d"]

    def test_local_node_excluded_from_tokens_file(self, tmp_path, monkeypatch):
        """The ``local`` entry's token belongs in ``agent.env``, NOT
        the sidecar — duplicating it here would create drift.
        """
        nodes_file, tokens_file = self._patch_paths(monkeypatch, tmp_path)
        # Pre-seed an agent.env so _populate_local_token has a value.
        agent_env = tmp_path / "agent.env"
        agent_env.write_text("LLAUNCHER_AGENT_TOKEN=local-secret\n")
        monkeypatch.setattr(
            "llauncher.core.agent_token.default_env_path", lambda: agent_env
        )

        reg = NodeRegistry()
        reg._nodes.clear()
        reg.add_node("local", "localhost", 8765)
        # Self-heal will stamp local-secret onto the local node.
        # When we add a remote, the save must EXCLUDE local from the
        # sidecar even though local.api_key is now non-None.
        reg.add_node("remote-e", "192.168.1.54", 8765, api_key="tok-E")

        payload = json.loads(tokens_file.read_text())
        assert "local" not in payload
        assert payload == {"remote-e": "tok-E"}

    def test_remove_node_drops_token_entry(self, tmp_path, monkeypatch):
        """``remove_node`` triggers _save → _save_node_tokens full-
        rewrite → removed node's token entry falls out automatically.
        """
        _, tokens_file = self._patch_paths(monkeypatch, tmp_path)

        reg = NodeRegistry()
        reg._nodes.clear()
        reg.add_node("remote-f", "192.168.1.55", 8765, api_key="tok-F")
        reg.add_node("remote-g", "192.168.1.56", 8765, api_key="tok-G")

        ok, _ = reg.remove_node("remote-f")
        assert ok

        payload = json.loads(tokens_file.read_text())
        assert "remote-f" not in payload
        assert payload.get("remote-g") == "tok-G"

    def test_missing_tokens_file_leaves_api_keys_none(self, tmp_path, monkeypatch):
        """nodes.json says has_api_key=True but the sidecar is missing
        → load succeeds, that remote's api_key is None. No synthesis
        from the local agent.env (credential-confusion guard).
        """
        nodes_file, tokens_file = self._patch_paths(monkeypatch, tmp_path)
        # nodes.json claims a token for remote-h.
        nodes_file.write_text(json.dumps({
            "remote-h": {
                "name": "remote-h", "host": "192.168.1.57",
                "port": 8765, "timeout": 5.0, "has_api_key": True,
            }
        }))
        # And a local agent.env exists — but it must NOT bleed through.
        agent_env = tmp_path / "agent.env"
        agent_env.write_text("LLAUNCHER_AGENT_TOKEN=would-be-credential-confusion\n")
        monkeypatch.setattr(
            "llauncher.core.agent_token.default_env_path", lambda: agent_env
        )
        assert not tokens_file.exists()

        reg = NodeRegistry()
        node = reg.get_node("remote-h")

        assert node is not None
        assert node.api_key is None, (
            "remote node must not be auto-stamped with the local agent's "
            "token — credential-confusion guard"
        )

    def test_corrupt_tokens_file_does_not_break_load(self, tmp_path, monkeypatch):
        """Malformed JSON in node_tokens.json must degrade gracefully:
        registry loads, remote api_keys are None, no exception.
        """
        nodes_file, tokens_file = self._patch_paths(monkeypatch, tmp_path)
        nodes_file.write_text(json.dumps({
            "remote-i": {
                "name": "remote-i", "host": "192.168.1.58",
                "port": 8765, "timeout": 5.0, "has_api_key": True,
            }
        }))
        tokens_file.write_text("{not valid json")

        # No exception:
        reg = NodeRegistry()
        node = reg.get_node("remote-i")
        assert node is not None
        assert node.api_key is None

    def test_save_tokens_chmod_failure_logs_but_does_not_raise(self, tmp_path, monkeypatch):
        """OSError from os.chmod on the sidecar is logged-and-swallowed,
        matching the existing nodes.json chmod failure posture.
        """
        from llauncher.remote import registry as registry_mod

        self._patch_paths(monkeypatch, tmp_path)
        real_chmod = os.chmod

        def failing_chmod(path, mode, **kwargs):
            # Only fail for the sidecar file write; let dir chmod pass.
            # **kwargs absorbs follow_symlinks=True from Path.chmod →
            # os.chmod (Python 3.12+).
            if str(path).endswith("node_tokens.json"):
                raise OSError("simulated permission denied")
            real_chmod(path, mode, **kwargs)

        monkeypatch.setattr("os.chmod", failing_chmod)

        reg = registry_mod.NodeRegistry()
        reg._nodes.clear()
        # Should not raise:
        ok, _ = reg.add_node("remote-j", "192.168.1.59", 8765, api_key="tok-J")
        assert ok
