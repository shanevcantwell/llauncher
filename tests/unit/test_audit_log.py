"""Unit tests for ``llauncher.core.audit_log``.

Per ADR-008. Verifies append-only semantics, commanded-vs-observed action
distinction, and resilience to corrupt log lines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llauncher.core import audit_log as al
from llauncher.core.audit_log import AuditAction, AuditEntry, AuditResult


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


# ---------------------------------------------------------------------------
# record + append_entry
# ---------------------------------------------------------------------------


def test_record_creates_file(audit_path: Path) -> None:
    al.record(
        AuditAction.STARTED,
        AuditResult.SUCCESS,
        caller="cli",
        port=8081,
        model="mistral-7b",
        pid=12345,
        path=audit_path,
    )
    assert audit_path.exists()


def test_record_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "audit.jsonl"

    al.record(AuditAction.STARTED, AuditResult.SUCCESS, caller="cli", path=nested)

    assert nested.exists()


def test_record_appends_one_line_per_call(audit_path: Path) -> None:
    al.record(AuditAction.STARTED, AuditResult.SUCCESS, caller="cli", port=8081, path=audit_path)
    al.record(AuditAction.STOPPED, AuditResult.SUCCESS, caller="cli", port=8081, path=audit_path)

    lines = audit_path.read_text().splitlines()
    assert len(lines) == 2


def test_record_writes_valid_json_with_enum_values(audit_path: Path) -> None:
    al.record(
        AuditAction.STARTED,
        AuditResult.SUCCESS,
        caller="cli",
        port=8081,
        model="mistral-7b",
        path=audit_path,
    )

    payload = json.loads(audit_path.read_text().splitlines()[0])
    assert payload["action"] == "started"
    assert payload["result"] == "success"
    assert payload["caller"] == "cli"
    assert payload["port"] == 8081
    assert payload["model"] == "mistral-7b"
    assert "timestamp" in payload


def test_record_returns_entry_with_timestamp(audit_path: Path) -> None:
    entry = al.record(
        AuditAction.STARTED,
        AuditResult.SUCCESS,
        caller="cli",
        path=audit_path,
    )
    assert isinstance(entry, AuditEntry)
    assert entry.timestamp  # ISO string, non-empty


# ---------------------------------------------------------------------------
# read_entries
# ---------------------------------------------------------------------------


def test_read_entries_empty_when_absent(audit_path: Path) -> None:
    assert al.read_entries(path=audit_path) == []


def test_read_entries_roundtrip(audit_path: Path) -> None:
    al.record(
        AuditAction.STARTED,
        AuditResult.SUCCESS,
        caller="cli",
        port=8081,
        model="mistral-7b",
        path=audit_path,
    )

    entries = al.read_entries(path=audit_path)

    assert len(entries) == 1
    assert entries[0].action == AuditAction.STARTED
    assert entries[0].result == AuditResult.SUCCESS
    assert entries[0].port == 8081
    assert entries[0].model == "mistral-7b"


def test_read_entries_preserves_order(audit_path: Path) -> None:
    for i in range(3):
        al.record(
            AuditAction.STARTED,
            AuditResult.SUCCESS,
            caller="cli",
            port=8081 + i,
            path=audit_path,
        )

    entries = al.read_entries(path=audit_path)

    assert [e.port for e in entries] == [8081, 8082, 8083]


def test_read_entries_skips_corrupt_lines(audit_path: Path) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        '{"timestamp":"x","action":"started","result":"success","caller":"c"}\n'
        "not valid json\n"
        '{"timestamp":"x","action":"stopped","result":"success","caller":"c"}\n'
    )

    entries = al.read_entries(path=audit_path)

    assert len(entries) == 2
    assert entries[0].action == AuditAction.STARTED
    assert entries[1].action == AuditAction.STOPPED


def test_read_entries_skips_blank_lines(audit_path: Path) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        '{"timestamp":"x","action":"started","result":"success","caller":"c"}\n'
        "\n"
        '{"timestamp":"x","action":"stopped","result":"success","caller":"c"}\n'
    )

    entries = al.read_entries(path=audit_path)

    assert len(entries) == 2


def test_read_entries_skips_unknown_enum_values(audit_path: Path) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        '{"timestamp":"x","action":"started","result":"success","caller":"c"}\n'
        '{"timestamp":"x","action":"BOGUS","result":"success","caller":"c"}\n'
    )

    entries = al.read_entries(path=audit_path)

    assert len(entries) == 1
    assert entries[0].action == AuditAction.STARTED


def test_read_entries_limit_returns_tail(audit_path: Path) -> None:
    for i in range(5):
        al.record(
            AuditAction.STARTED,
            AuditResult.SUCCESS,
            caller="cli",
            port=8081 + i,
            path=audit_path,
        )

    last_two = al.read_entries(path=audit_path, limit=2)

    assert [e.port for e in last_two] == [8084, 8085]


# ---------------------------------------------------------------------------
# Action discrimination
# ---------------------------------------------------------------------------


def test_observed_actions_distinct_from_commanded(audit_path: Path) -> None:
    al.record(
        AuditAction.STOPPED,
        AuditResult.SUCCESS,
        caller="cli",
        port=8081,
        path=audit_path,
    )
    al.record(
        AuditAction.OBSERVED_STOPPED,
        AuditResult.SUCCESS,
        caller="reconcile",
        port=8082,
        message="pid not alive",
        path=audit_path,
    )

    entries = al.read_entries(path=audit_path)

    actions = [e.action for e in entries]
    assert AuditAction.STOPPED in actions
    assert AuditAction.OBSERVED_STOPPED in actions
    callers = {e.caller for e in entries}
    assert callers == {"cli", "reconcile"}


def test_swap_entry_carries_from_and_to_models(audit_path: Path) -> None:
    al.record(
        AuditAction.SWAPPED,
        AuditResult.SUCCESS,
        caller="mcp",
        port=8081,
        from_model="mistral-7b",
        model="llama-3-8b",
        pid=99999,
        path=audit_path,
    )

    entry = al.read_entries(path=audit_path)[0]
    assert entry.from_model == "mistral-7b"
    assert entry.model == "llama-3-8b"
    assert entry.action == AuditAction.SWAPPED
