"""Unit tests for ``llauncher.operations.reconcile`` (issue #201 Part 2a).

A server that spawns then exits immediately leaves a ``{port}.lock`` behind
even though it is gone from the process table. The status-path sweep prunes
those stale claims and emits one ``OBSERVED_STOPPED`` audit entry each, while
leaving lockfiles for still-live pids untouched.
"""

from __future__ import annotations

import os
from pathlib import Path

import psutil
import pytest

from llauncher.core import audit_log
from llauncher.core import lockfile as lf
from llauncher.operations.reconcile import reconcile_stale_lockfiles

# A pid that is essentially never live. ``is_pid_alive`` returns False
# (NoSuchProcess), so a lockfile claiming it reconciles as stale.
_DEAD_PID = 999_999


@pytest.fixture
def state_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the run dir and audit log at a tmp location for the sweep."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr("llauncher.core.lockfile.LAUNCHER_RUN_DIR", run_dir)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_RUN_DIR", run_dir)
    monkeypatch.setattr(
        "llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", tmp_path / "audit.jsonl"
    )
    return run_dir


def test_removes_stale_lockfile_and_returns_it(state_dirs: Path) -> None:
    """A lockfile claiming a dead pid is removed and returned."""
    lf.write_lockfile(8081, "mistral-7b", _DEAD_PID, run_dir=state_dirs)
    assert (state_dirs / "8081.lock").exists()

    pruned = reconcile_stale_lockfiles(caller="status")

    assert [p.port for p in pruned] == [8081]
    assert pruned[0].model == "mistral-7b"
    assert not (state_dirs / "8081.lock").exists()


def test_keeps_live_lockfile(state_dirs: Path) -> None:
    """A lockfile claiming the live test pid is left untouched."""
    lf.write_lockfile(8082, "qwen", os.getpid(), run_dir=state_dirs)

    pruned = reconcile_stale_lockfiles()

    assert pruned == []
    assert (state_dirs / "8082.lock").exists()


def test_mixed_prunes_only_dead(state_dirs: Path) -> None:
    """Only the dead-pid claim is pruned; the live one survives."""
    lf.write_lockfile(8081, "dead-model", _DEAD_PID, run_dir=state_dirs)
    lf.write_lockfile(8082, "live-model", os.getpid(), run_dir=state_dirs)

    pruned = reconcile_stale_lockfiles()

    assert [p.port for p in pruned] == [8081]
    assert not (state_dirs / "8081.lock").exists()
    assert (state_dirs / "8082.lock").exists()


def test_emits_one_observed_stopped_audit_entry(state_dirs: Path) -> None:
    """Each pruned lockfile produces exactly one OBSERVED_STOPPED entry."""
    lf.write_lockfile(8081, "mistral-7b", _DEAD_PID, run_dir=state_dirs)

    reconcile_stale_lockfiles(caller="status")

    entries = audit_log.read_entries()
    observed = [
        e
        for e in entries
        if e.action is audit_log.AuditAction.OBSERVED_STOPPED
    ]
    assert len(observed) == 1
    assert observed[0].port == 8081
    assert observed[0].caller == "status"
    assert observed[0].pid == _DEAD_PID


def test_idempotent_no_double_emit(state_dirs: Path) -> None:
    """A second sweep finds nothing — the pruned lockfile is already gone."""
    lf.write_lockfile(8081, "mistral-7b", _DEAD_PID, run_dir=state_dirs)

    first = reconcile_stale_lockfiles()
    second = reconcile_stale_lockfiles()

    assert [p.port for p in first] == [8081]
    assert second == []
    observed = [
        e
        for e in audit_log.read_entries()
        if e.action is audit_log.AuditAction.OBSERVED_STOPPED
    ]
    assert len(observed) == 1


def test_keeps_access_denied_lockfile(
    state_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lockfile whose pid is present-but-unreadable is NOT pruned (#208).

    Under system-mode (#191/#194) the sweep runs as a different uid than the
    ``llama-server`` it spawned, so ``psutil`` raises ``AccessDenied`` reading
    a live process. ``is_pid_alive`` treats that as alive, so the sweep must
    leave the lockfile of a running cross-uid server in place.
    """
    lf.write_lockfile(8083, "cross-uid-model", 4242, run_dir=state_dirs)

    def _raise_access_denied(_pid: int) -> psutil.Process:
        raise psutil.AccessDenied(pid=_pid)

    monkeypatch.setattr(lf.psutil, "Process", _raise_access_denied)

    pruned = reconcile_stale_lockfiles()

    assert pruned == []
    assert (state_dirs / "8083.lock").exists()
    assert audit_log.read_entries() == []


def test_sweep_keeps_access_denied_prunes_dead(
    state_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In one sweep: an unreadable (live) pid survives, a gone pid is pruned.

    This is the #208 blast-radius guard — a single ``AccessDenied`` pid must
    not cause the sweep to clear other ports, and genuinely-dead claims still
    reconcile as before.
    """
    lf.write_lockfile(8081, "dead-model", _DEAD_PID, run_dir=state_dirs)
    lf.write_lockfile(8083, "cross-uid-model", 4242, run_dir=state_dirs)

    def _fake_process(pid: int) -> psutil.Process:
        if pid == 4242:
            raise psutil.AccessDenied(pid=pid)
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(lf.psutil, "Process", _fake_process)

    pruned = reconcile_stale_lockfiles()

    assert [p.port for p in pruned] == [8081]
    assert not (state_dirs / "8081.lock").exists()
    assert (state_dirs / "8083.lock").exists()


def test_empty_run_dir_is_noop(state_dirs: Path) -> None:
    """No lockfiles → empty result, no audit entries."""
    assert reconcile_stale_lockfiles() == []
    assert audit_log.read_entries() == []
