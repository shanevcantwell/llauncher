"""Regression guard for issue #308 — ``audit_log.record()`` write failures
must never propagate.

Audit logging is best-effort observability: every ``operations/*`` verb
calls ``al.record(...)`` as a side-channel record of what it did. Before
this fix, an ``OSError`` from ``append_entry`` (disk full, permissions,
missing/unwritable ``LAUNCHER_AUDIT_PATH``, ...) propagated straight out of
``record()`` and up through the calling operation — turning an
observability outage into a functional outage, and (via
``operations/start.py``'s uncaught propagation) into the "silent 500"
described in issue #308.

``record()`` now catches ``OSError`` around the write, logs it, and still
returns the (unpersisted) ``AuditEntry`` so callers that inspect the return
value keep working identically to the success path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from llauncher.core import audit_log as al
from llauncher.core.audit_log import AuditAction, AuditEntry, AuditResult


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


def test_record_does_not_raise_on_write_failure(audit_path: Path, caplog) -> None:
    with patch(
        "llauncher.core.audit_log.append_entry",
        side_effect=OSError("disk full"),
    ):
        with caplog.at_level("ERROR"):
            entry = al.record(
                AuditAction.STARTED,
                AuditResult.SUCCESS,
                caller="test",
                port=8081,
                model="mistral-7b",
                path=audit_path,
            )

    # Returned entry reflects what was requested, even though nothing
    # was persisted.
    assert isinstance(entry, AuditEntry)
    assert entry.action == AuditAction.STARTED
    assert entry.result == AuditResult.SUCCESS
    assert entry.port == 8081
    assert entry.model == "mistral-7b"

    # Nothing was actually written.
    assert not audit_path.exists()

    # A failure was logged, not silently swallowed.
    assert any(
        record.levelname == "ERROR" and "audit" in record.message.lower()
        for record in caplog.records
    )


def test_record_still_writes_on_the_happy_path(audit_path: Path) -> None:
    """Sanity check: the try/except does not change success-path behavior."""
    entry = al.record(
        AuditAction.STOPPED,
        AuditResult.SUCCESS,
        caller="test",
        port=8082,
        path=audit_path,
    )
    assert audit_path.exists()
    entries = al.read_entries(path=audit_path)
    assert len(entries) == 1
    assert entries[0].action == entry.action
