"""Tests for ADR-015 orphan discovery (operations.orphan + state wiring)."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llauncher.core import lockfile as lf
from llauncher.operations import orphan as orphan_ops
from llauncher.operations.orphan import OrphanInfo


def _fake_proc(pid: int) -> MagicMock:
    """Build a minimal psutil.Process stand-in for the annotated scan."""
    m = MagicMock()
    m.pid = pid
    return m


# ---------------------------------------------------------------------------
# find_all_llama_servers_annotated — port extraction and unreadable cmdline
# ---------------------------------------------------------------------------


class TestAnnotatedScan:
    def test_extracts_port_from_argv(self):
        from llauncher.core import process as proc

        proc_a = _fake_proc(1001)
        proc_a.name.return_value = "llama-server"
        proc_a.cmdline.return_value = ["llama-server", "--port", "8081", "-m", "/a.gguf"]

        with patch.object(proc.psutil, "process_iter", return_value=[proc_a]):
            result = proc.find_all_llama_servers_annotated()

        assert len(result) == 1
        p, port, unreadable = result[0]
        assert p.pid == 1001
        assert port == 8081
        assert unreadable is False

    def test_port_none_when_no_port_arg(self):
        from llauncher.core import process as proc

        p = _fake_proc(1002)
        p.name.return_value = "llama-server"
        p.cmdline.return_value = ["llama-server", "-m", "/a.gguf"]

        with patch.object(proc.psutil, "process_iter", return_value=[p]):
            result = proc.find_all_llama_servers_annotated()

        assert result == [(p, None, False)]

    def test_access_denied_yields_unreadable(self):
        from llauncher.core import process as proc

        p = _fake_proc(1003)
        p.name.return_value = "llama-server"
        p.cmdline.side_effect = proc.psutil.AccessDenied()

        with patch.object(proc.psutil, "process_iter", return_value=[p]):
            result = proc.find_all_llama_servers_annotated()

        assert len(result) == 1
        proc_obj, port, unreadable = result[0]
        assert port is None
        assert unreadable is True

    def test_non_llama_process_skipped(self):
        from llauncher.core import process as proc

        p = _fake_proc(1004)
        p.name.return_value = "nginx"
        p.cmdline.return_value = ["nginx", "-c", "/etc/nginx.conf"]

        with patch.object(proc.psutil, "process_iter", return_value=[p]):
            result = proc.find_all_llama_servers_annotated()

        assert result == []

    def test_no_such_process_skipped(self):
        from llauncher.core import process as proc

        p = _fake_proc(1005)
        p.name.side_effect = proc.psutil.NoSuchProcess(pid=1005)

        with patch.object(proc.psutil, "process_iter", return_value=[p]):
            # NoSuchProcess can fire from name() too; the outer try
            # catches it without raising.
            result = proc.find_all_llama_servers_annotated()

        assert result == []


# ---------------------------------------------------------------------------
# list_orphans — managed-vs-unmanaged classification
# ---------------------------------------------------------------------------


class TestListOrphans:
    def test_no_processes_returns_empty(self, tmp_path):
        with patch(
            "llauncher.operations.orphan.proc.find_all_llama_servers_annotated",
            return_value=[],
        ):
            assert orphan_ops.list_orphans() == []

    def test_managed_process_excluded(self, tmp_path, monkeypatch):
        """Process whose (port, pid) matches a live lockfile is managed."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        lf.write_lockfile(8081, "model-a", 2001, run_dir=run_dir)

        monkeypatch.setattr(
            "llauncher.core.lockfile.LAUNCHER_RUN_DIR", run_dir
        )
        monkeypatch.setattr(
            "llauncher.core.settings.LAUNCHER_RUN_DIR", run_dir
        )

        fake = _fake_proc(2001)
        with patch(
            "llauncher.operations.orphan.proc.find_all_llama_servers_annotated",
            return_value=[(fake, 8081, False)],
        ), patch(
            "llauncher.operations.orphan.lf.is_pid_alive", return_value=True
        ):
            result = orphan_ops.list_orphans()

        assert result == []

    def test_no_lockfile_yields_orphan(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        monkeypatch.setattr(
            "llauncher.core.lockfile.LAUNCHER_RUN_DIR", run_dir
        )

        fake = _fake_proc(2002)
        with patch(
            "llauncher.operations.orphan.proc.find_all_llama_servers_annotated",
            return_value=[(fake, 8082, False)],
        ):
            result = orphan_ops.list_orphans()

        assert result == [OrphanInfo(pid=2002, port=8082)]

    def test_stale_lockfile_yields_orphan(self, tmp_path, monkeypatch):
        """Lockfile points at dead pid → observed pid is unmanaged."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        lf.write_lockfile(8083, "model-b", 9999, run_dir=run_dir)
        monkeypatch.setattr(
            "llauncher.core.lockfile.LAUNCHER_RUN_DIR", run_dir
        )

        fake = _fake_proc(2003)
        with patch(
            "llauncher.operations.orphan.proc.find_all_llama_servers_annotated",
            return_value=[(fake, 8083, False)],
        ), patch(
            "llauncher.operations.orphan.lf.is_pid_alive", return_value=False
        ):
            result = orphan_ops.list_orphans()

        assert result == [OrphanInfo(pid=2003, port=8083)]

    def test_pid_mismatch_yields_orphan(self, tmp_path, monkeypatch):
        """Lockfile claims different live pid → observed pid is unmanaged."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        lf.write_lockfile(8084, "model-c", 4000, run_dir=run_dir)
        monkeypatch.setattr(
            "llauncher.core.lockfile.LAUNCHER_RUN_DIR", run_dir
        )

        fake = _fake_proc(2004)
        with patch(
            "llauncher.operations.orphan.proc.find_all_llama_servers_annotated",
            return_value=[(fake, 8084, False)],
        ), patch(
            "llauncher.operations.orphan.lf.is_pid_alive", return_value=True
        ):
            result = orphan_ops.list_orphans()

        assert result == [OrphanInfo(pid=2004, port=8084)]

    def test_unreadable_cmdline_yields_orphan(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        monkeypatch.setattr(
            "llauncher.core.lockfile.LAUNCHER_RUN_DIR", run_dir
        )

        fake = _fake_proc(2005)
        with patch(
            "llauncher.operations.orphan.proc.find_all_llama_servers_annotated",
            return_value=[(fake, None, True)],
        ):
            result = orphan_ops.list_orphans()

        assert result == [
            OrphanInfo(pid=2005, port=None, cmdline_unreadable=True)
        ]

    def test_orphans_sorted_by_pid(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        monkeypatch.setattr(
            "llauncher.core.lockfile.LAUNCHER_RUN_DIR", run_dir
        )

        procs = [
            (_fake_proc(3003), 8085, False),
            (_fake_proc(3001), 8086, False),
            (_fake_proc(3002), 8087, False),
        ]
        with patch(
            "llauncher.operations.orphan.proc.find_all_llama_servers_annotated",
            return_value=procs,
        ):
            result = orphan_ops.list_orphans()

        assert [o.pid for o in result] == [3001, 3002, 3003]


# ---------------------------------------------------------------------------
# LauncherState.refresh_orphans — audit dedupe + warning dedupe
# ---------------------------------------------------------------------------


class TestStateRefreshOrphans:
    def _bare_state(self):
        """Build a LauncherState without running __post_init__'s refresh."""
        from llauncher.state import LauncherState

        s = LauncherState.__new__(LauncherState)
        s.models = {}
        s.running = {}
        s.audit = []
        from llauncher.models.config import ChangeRules

        s.rules = ChangeRules()
        s.orphans = []
        s._observed_orphan_pids = set()
        s._warned_unreadable_pids = set()
        return s

    def test_first_sighting_emits_audit_and_sets_dedupe(self):
        s = self._bare_state()
        scan = [OrphanInfo(pid=4001, port=8081)]

        with patch(
            "llauncher.state.list_orphans", return_value=scan
        ), patch(
            "llauncher.state.record_observed_orphan"
        ) as rec:
            s.refresh_orphans()

        rec.assert_called_once_with(scan[0])
        assert s._observed_orphan_pids == {4001}
        assert s.orphans == scan

    def test_repeat_sighting_does_not_re_emit(self):
        s = self._bare_state()
        scan = [OrphanInfo(pid=4002, port=8082)]

        with patch(
            "llauncher.state.list_orphans", return_value=scan
        ), patch(
            "llauncher.state.record_observed_orphan"
        ) as rec:
            s.refresh_orphans()  # first
            s.refresh_orphans()  # second

        assert rec.call_count == 1

    def test_pid_disappears_then_reappears_re_emits(self):
        s = self._bare_state()
        first = [OrphanInfo(pid=4003, port=8083)]
        empty: list[OrphanInfo] = []

        with patch(
            "llauncher.state.list_orphans", side_effect=[first, empty, first]
        ), patch(
            "llauncher.state.record_observed_orphan"
        ) as rec:
            s.refresh_orphans()  # first sighting → emit
            s.refresh_orphans()  # disappeared → prune
            s.refresh_orphans()  # reappeared → emit again

        assert rec.call_count == 2

    def test_unreadable_warning_deduped(self, caplog):
        s = self._bare_state()
        scan = [OrphanInfo(pid=4004, port=None, cmdline_unreadable=True)]

        with patch(
            "llauncher.state.list_orphans", return_value=scan
        ), patch(
            "llauncher.state.record_observed_orphan"
        ) as rec, caplog.at_level(logging.WARNING, logger="llauncher.state"):
            s.refresh_orphans()
            s.refresh_orphans()

        # Audit never emitted for unreadable pids.
        rec.assert_not_called()
        # Warning logged exactly once.
        warn_lines = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_lines) == 1
        assert "4004" in warn_lines[0].getMessage()

    def test_unreadable_pid_disappears_clears_dedupe(self):
        s = self._bare_state()
        scan = [OrphanInfo(pid=4005, port=None, cmdline_unreadable=True)]

        with patch(
            "llauncher.state.list_orphans", side_effect=[scan, [], scan]
        ):
            s.refresh_orphans()
            assert 4005 in s._warned_unreadable_pids
            s.refresh_orphans()
            assert 4005 not in s._warned_unreadable_pids
            s.refresh_orphans()
            assert 4005 in s._warned_unreadable_pids


# ---------------------------------------------------------------------------
# HTTP /orphans endpoint + /status orphans field
# ---------------------------------------------------------------------------


class TestOrphansEndpoint:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from llauncher.agent.server import create_app_unauthenticated

        return TestClient(create_app_unauthenticated())

    @pytest.fixture(autouse=True)
    def _reset_state(self, client):
        from llauncher.agent import routing

        routing._state = None
        yield
        routing._state = None

    def test_get_orphans_empty(self, client, monkeypatch):
        from llauncher.agent import routing

        # Stub out state — no orphans, no refresh side effects.
        fake_state = MagicMock()
        fake_state.orphans = []
        monkeypatch.setattr(routing, "get_state", lambda: fake_state)

        resp = client.get("/orphans")
        assert resp.status_code == 200
        body = resp.json()
        assert body["orphans"] == []
        assert body["total"] == 0
        assert "node" in body

    def test_get_orphans_populated(self, client, monkeypatch):
        from llauncher.agent import routing

        fake_state = MagicMock()
        fake_state.orphans = [
            OrphanInfo(pid=5001, port=8081),
            OrphanInfo(pid=5002, port=None, cmdline_unreadable=True),
        ]
        monkeypatch.setattr(routing, "get_state", lambda: fake_state)

        resp = client.get("/orphans")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert {o["pid"] for o in body["orphans"]} == {5001, 5002}
        # cmdline_unreadable serialized correctly.
        unreadable = [o for o in body["orphans"] if o["pid"] == 5002][0]
        assert unreadable["cmdline_unreadable"] is True
        assert unreadable["port"] is None


# ---------------------------------------------------------------------------
# MCP list_orphans tool
# ---------------------------------------------------------------------------


class TestMcpListOrphans:
    @pytest.mark.asyncio
    async def test_returns_envelope(self):
        from llauncher.mcp_server.tools.servers import list_orphans

        state = MagicMock()
        state.orphans = [OrphanInfo(pid=6001, port=8081)]

        result = await list_orphans(state, {})

        state.refresh_orphans.assert_called_once()
        assert result["total"] == 1
        assert result["orphans"][0]["pid"] == 6001

    def test_in_get_tools(self):
        from llauncher.mcp_server.tools.servers import get_tools

        names = {t.name for t in get_tools()}
        assert "list_orphans" in names


# ---------------------------------------------------------------------------
# CLI llauncher orphan list
# ---------------------------------------------------------------------------


class TestCliOrphanList:
    def test_list_empty(self):
        from typer.testing import CliRunner
        from llauncher.cli import app

        runner = CliRunner()
        with patch("llauncher.operations.list_orphans", return_value=[]):
            result = runner.invoke(app, ["orphan", "list"])

        assert result.exit_code == 0
        assert "no orphan" in result.stdout.lower()

    def test_list_populated_table(self):
        from typer.testing import CliRunner
        from llauncher.cli import app

        runner = CliRunner()
        orphans = [
            OrphanInfo(pid=7001, port=8081),
            OrphanInfo(pid=7002, port=None, cmdline_unreadable=True),
        ]
        with patch("llauncher.operations.list_orphans", return_value=orphans):
            result = runner.invoke(app, ["orphan", "list"])

        assert result.exit_code == 0
        assert "7001" in result.stdout
        assert "7002" in result.stdout

    def test_list_json(self):
        from typer.testing import CliRunner
        from llauncher.cli import app
        import json as _json

        runner = CliRunner()
        orphans = [OrphanInfo(pid=7003, port=8082)]
        with patch("llauncher.operations.list_orphans", return_value=orphans):
            result = runner.invoke(app, ["orphan", "list", "--json"])

        assert result.exit_code == 0
        payload = _json.loads(result.stdout)
        assert payload == [
            {"pid": 7003, "port": 8082, "cmdline_unreadable": False}
        ]
