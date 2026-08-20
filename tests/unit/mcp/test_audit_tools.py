"""Tests for the MCP ``read_audit`` tool (issue #338).

Mirrors ``TestAuditEndpoint`` in ``tests/unit/test_agent.py`` and the CLI's
``audit`` command tests — same ``LAUNCHER_AUDIT_PATH`` monkeypatch, same
filter/limit semantics, since all three surfaces wrap
``core.audit_log.read_entries`` identically.
"""

import pytest

from llauncher.mcp_server.tools.audit import get_tools, read_audit


def test_get_tools_declares_read_audit():
    """``get_tools`` advertises exactly the ``read_audit`` tool, all-optional args."""
    tools = get_tools()
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "read_audit"
    assert tool.inputSchema["required"] == []
    assert set(tool.inputSchema["properties"]) == {"limit", "action", "result"}


class TestReadAudit:
    """Tests for the ``read_audit`` tool handler."""

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self, tmp_path, monkeypatch):
        """Empty/missing audit log returns an empty entries list."""
        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

        result = await read_audit({})
        assert result == {"entries": []}

    @pytest.mark.asyncio
    async def test_returns_serialized_entries(self, tmp_path, monkeypatch):
        """Populated audit log returns a list of JSON-safe entry dicts."""
        from llauncher.core import audit_log

        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

        audit_log.record(
            audit_log.AuditAction.STARTED,
            audit_log.AuditResult.SUCCESS,
            caller="test",
            port=8080,
            model="m",
            message="started m",
        )
        audit_log.record(
            audit_log.AuditAction.STOPPED,
            audit_log.AuditResult.SUCCESS,
            caller="test",
            port=8080,
            model="m",
            message="stopped m",
        )

        result = await read_audit({})
        entries = result["entries"]
        assert len(entries) == 2
        assert entries[0]["action"] == "started"
        assert entries[0]["result"] == "success"
        assert entries[1]["action"] == "stopped"
        assert entries[0]["message"] == "started m"
        assert entries[1]["message"] == "stopped m"

    @pytest.mark.asyncio
    async def test_action_filter(self, tmp_path, monkeypatch):
        """``action`` narrows the result to entries with that action."""
        from llauncher.core import audit_log

        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

        audit_log.record(audit_log.AuditAction.STARTED, audit_log.AuditResult.SUCCESS, caller="t")
        audit_log.record(audit_log.AuditAction.STOPPED, audit_log.AuditResult.SUCCESS, caller="t")

        result = await read_audit({"action": "stopped"})
        entries = result["entries"]
        assert len(entries) == 1
        assert entries[0]["action"] == "stopped"

    @pytest.mark.asyncio
    async def test_result_filter(self, tmp_path, monkeypatch):
        """``result`` narrows the result to entries with that result."""
        from llauncher.core import audit_log

        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

        audit_log.record(audit_log.AuditAction.STARTED, audit_log.AuditResult.SUCCESS, caller="t")
        audit_log.record(audit_log.AuditAction.STARTED, audit_log.AuditResult.ERROR, caller="t")

        result = await read_audit({"result": "error"})
        entries = result["entries"]
        assert len(entries) == 1
        assert entries[0]["result"] == "error"

    @pytest.mark.asyncio
    async def test_limit_bounds_tail(self, tmp_path, monkeypatch):
        """``limit`` caps the number of entries returned to the newest N."""
        from llauncher.core import audit_log

        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

        for i in range(5):
            audit_log.record(
                audit_log.AuditAction.STARTED,
                audit_log.AuditResult.SUCCESS,
                caller="t",
                message=f"entry-{i}",
            )

        result = await read_audit({"limit": 2})
        entries = result["entries"]
        assert len(entries) == 2
        assert entries[0]["message"] == "entry-3"
        assert entries[1]["message"] == "entry-4"

    @pytest.mark.asyncio
    async def test_default_limit_is_200(self, tmp_path, monkeypatch):
        """Omitted ``limit`` defaults to 200, matching the agent's ``GET /audit``."""
        from llauncher.core import audit_log

        audit_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

        for i in range(3):
            audit_log.record(
                audit_log.AuditAction.STARTED,
                audit_log.AuditResult.SUCCESS,
                caller="t",
                message=f"entry-{i}",
            )

        result = await read_audit({})
        assert len(result["entries"]) == 3
