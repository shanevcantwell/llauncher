"""Unit tests for ``llauncher.core.lockfile``.

Per ADR-008. Verifies atomic claim semantics, reconciliation rules, and
corrupt-file resilience.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from llauncher.core import lockfile as lf


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "run"


# ---------------------------------------------------------------------------
# write_lockfile
# ---------------------------------------------------------------------------


def test_write_creates_file_and_returns_lockfile(run_dir: Path) -> None:
    result = lf.write_lockfile(8081, "mistral-7b", 12345, run_dir=run_dir)

    assert result.port == 8081
    assert result.model == "mistral-7b"
    assert result.pid == 12345
    assert result.llauncher_pid == os.getpid()
    assert (run_dir / "8081.lock").exists()


def test_write_persists_valid_json(run_dir: Path) -> None:
    lf.write_lockfile(8081, "mistral-7b", 12345, run_dir=run_dir)

    data = json.loads((run_dir / "8081.lock").read_text())
    assert data["port"] == 8081
    assert data["model"] == "mistral-7b"
    assert data["pid"] == 12345
    assert data["llauncher_pid"] == os.getpid()
    assert "started_at" in data


def test_write_creates_run_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "run"
    assert not nested.exists()

    lf.write_lockfile(8081, "m", 1, run_dir=nested)

    assert nested.exists()


def test_write_fails_if_lockfile_exists(run_dir: Path) -> None:
    lf.write_lockfile(8081, "mistral-7b", 12345, run_dir=run_dir)

    with pytest.raises(FileExistsError):
        lf.write_lockfile(8081, "llama-3", 67890, run_dir=run_dir)


# ---------------------------------------------------------------------------
# read_lockfile
# ---------------------------------------------------------------------------


def test_read_returns_written_lockfile(run_dir: Path) -> None:
    lf.write_lockfile(8081, "mistral-7b", 12345, run_dir=run_dir)

    result = lf.read_lockfile(8081, run_dir=run_dir)

    assert result is not None
    assert result.port == 8081
    assert result.model == "mistral-7b"
    assert result.pid == 12345


def test_read_returns_none_when_absent(run_dir: Path) -> None:
    assert lf.read_lockfile(8081, run_dir=run_dir) is None


def test_read_returns_none_for_corrupt_file(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "8081.lock").write_text("not valid json{")

    assert lf.read_lockfile(8081, run_dir=run_dir) is None


def test_read_returns_none_for_missing_keys(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "8081.lock").write_text(json.dumps({"port": 8081}))  # incomplete

    assert lf.read_lockfile(8081, run_dir=run_dir) is None


# ---------------------------------------------------------------------------
# remove_lockfile
# ---------------------------------------------------------------------------


def test_remove_returns_true_when_present(run_dir: Path) -> None:
    lf.write_lockfile(8081, "m", 1, run_dir=run_dir)

    assert lf.remove_lockfile(8081, run_dir=run_dir) is True
    assert not (run_dir / "8081.lock").exists()


def test_remove_returns_false_when_absent(run_dir: Path) -> None:
    assert lf.remove_lockfile(8081, run_dir=run_dir) is False


# ---------------------------------------------------------------------------
# list_lockfiles
# ---------------------------------------------------------------------------


def test_list_empty_when_dir_absent(tmp_path: Path) -> None:
    assert lf.list_lockfiles(run_dir=tmp_path / "nope") == []


def test_list_empty_when_dir_present_but_empty(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    assert lf.list_lockfiles(run_dir=run_dir) == []


def test_list_returns_all_valid_lockfiles(run_dir: Path) -> None:
    lf.write_lockfile(8081, "a", 1, run_dir=run_dir)
    lf.write_lockfile(8082, "b", 2, run_dir=run_dir)
    lf.write_lockfile(8083, "c", 3, run_dir=run_dir)

    result = lf.list_lockfiles(run_dir=run_dir)

    ports = sorted(r.port for r in result)
    assert ports == [8081, 8082, 8083]


def test_list_skips_corrupt_files(run_dir: Path) -> None:
    lf.write_lockfile(8081, "a", 1, run_dir=run_dir)
    (run_dir / "9999.lock").write_text("garbage{")

    result = lf.list_lockfiles(run_dir=run_dir)

    assert len(result) == 1
    assert result[0].port == 8081


# ---------------------------------------------------------------------------
# is_pid_alive
# ---------------------------------------------------------------------------


def test_is_pid_alive_true_for_self() -> None:
    assert lf.is_pid_alive(os.getpid()) is True


def test_is_pid_alive_false_for_likely_dead() -> None:
    # Use a sentinel pid that is overwhelmingly unlikely to exist on any
    # system. PID_MAX on Linux is typically 4194304; 2**31-1 exceeds that.
    assert lf.is_pid_alive(2**31 - 1) is False


# ---------------------------------------------------------------------------
# reconcile_lockfile
# ---------------------------------------------------------------------------


def test_reconcile_dead_pid(run_dir: Path) -> None:
    written = lf.write_lockfile(8081, "model", 2**31 - 1, run_dir=run_dir)

    result = lf.reconcile_lockfile(written)

    assert result.pid_alive is False
    assert result.is_ours is False


def test_reconcile_alive_pid_no_sentinel_check(run_dir: Path) -> None:
    written = lf.write_lockfile(8081, "model", os.getpid(), run_dir=run_dir)

    result = lf.reconcile_lockfile(written)

    assert result.pid_alive is True
    assert result.is_ours is True


def test_reconcile_alive_pid_with_passing_sentinel(run_dir: Path) -> None:
    written = lf.write_lockfile(8081, "model", os.getpid(), run_dir=run_dir)

    result = lf.reconcile_lockfile(written, sentinel_check=lambda pid: True)

    assert result.pid_alive is True
    assert result.is_ours is True


def test_reconcile_alive_pid_with_failing_sentinel(run_dir: Path) -> None:
    written = lf.write_lockfile(8081, "model", os.getpid(), run_dir=run_dir)

    result = lf.reconcile_lockfile(written, sentinel_check=lambda pid: False)

    assert result.pid_alive is True
    assert result.is_ours is False


def test_reconcile_dead_pid_does_not_call_sentinel(run_dir: Path) -> None:
    written = lf.write_lockfile(8081, "model", 2**31 - 1, run_dir=run_dir)
    sentinel_called = False

    def sentinel(pid: int) -> bool:
        nonlocal sentinel_called
        sentinel_called = True
        return True

    result = lf.reconcile_lockfile(written, sentinel_check=sentinel)

    assert result.pid_alive is False
    assert sentinel_called is False  # short-circuit on dead pid
