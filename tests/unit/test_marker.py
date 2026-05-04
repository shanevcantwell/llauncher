"""Unit tests for ``llauncher.core.marker``.

Per ADR-011. Verifies atomic claim semantics, staleness detection, and
corrupt-file resilience for the in-flight swap marker.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from llauncher.core import marker as mk


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "run"


# ---------------------------------------------------------------------------
# take_marker
# ---------------------------------------------------------------------------


def test_take_creates_file_and_returns_marker(run_dir: Path) -> None:
    result = mk.take_marker(
        8081,
        caller="mcp",
        from_model="mistral-7b",
        to_model="llama-3-8b",
        run_dir=run_dir,
    )

    assert result.port == 8081
    assert result.caller == "mcp"
    assert result.from_model == "mistral-7b"
    assert result.to_model == "llama-3-8b"
    assert result.llauncher_pid == os.getpid()
    assert (run_dir / "8081.swap").exists()


def test_take_persists_valid_json(run_dir: Path) -> None:
    mk.take_marker(
        8081,
        caller="cli",
        from_model="a",
        to_model="b",
        run_dir=run_dir,
    )

    data = json.loads((run_dir / "8081.swap").read_text())
    assert data["port"] == 8081
    assert data["caller"] == "cli"
    assert data["from_model"] == "a"
    assert data["to_model"] == "b"
    assert data["llauncher_pid"] == os.getpid()
    assert "started_at" in data


def test_take_creates_run_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "run"
    assert not nested.exists()

    mk.take_marker(
        8081, caller="cli", from_model="a", to_model="b", run_dir=nested
    )

    assert nested.exists()


def test_take_fails_if_marker_exists(run_dir: Path) -> None:
    mk.take_marker(
        8081, caller="cli", from_model="a", to_model="b", run_dir=run_dir
    )

    with pytest.raises(FileExistsError):
        mk.take_marker(
            8081, caller="mcp", from_model="a", to_model="c", run_dir=run_dir
        )


# ---------------------------------------------------------------------------
# read_marker
# ---------------------------------------------------------------------------


def test_read_returns_taken_marker(run_dir: Path) -> None:
    mk.take_marker(
        8081, caller="ui", from_model="a", to_model="b", run_dir=run_dir
    )

    result = mk.read_marker(8081, run_dir=run_dir)

    assert result is not None
    assert result.port == 8081
    assert result.caller == "ui"
    assert result.from_model == "a"
    assert result.to_model == "b"


def test_read_returns_none_when_absent(run_dir: Path) -> None:
    assert mk.read_marker(8081, run_dir=run_dir) is None


def test_read_returns_none_for_corrupt_file(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "8081.swap").write_text("not valid json{")

    assert mk.read_marker(8081, run_dir=run_dir) is None


def test_read_returns_none_for_missing_keys(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "8081.swap").write_text(json.dumps({"port": 8081}))  # incomplete

    assert mk.read_marker(8081, run_dir=run_dir) is None


# ---------------------------------------------------------------------------
# release_marker
# ---------------------------------------------------------------------------


def test_release_returns_true_when_present(run_dir: Path) -> None:
    mk.take_marker(
        8081, caller="cli", from_model="a", to_model="b", run_dir=run_dir
    )

    assert mk.release_marker(8081, run_dir=run_dir) is True
    assert not (run_dir / "8081.swap").exists()


def test_release_returns_false_when_absent(run_dir: Path) -> None:
    assert mk.release_marker(8081, run_dir=run_dir) is False


def test_release_then_take_succeeds(run_dir: Path) -> None:
    """A released marker frees the slot for a subsequent take."""
    mk.take_marker(
        8081, caller="cli", from_model="a", to_model="b", run_dir=run_dir
    )
    mk.release_marker(8081, run_dir=run_dir)

    # Should not raise.
    second = mk.take_marker(
        8081, caller="mcp", from_model="b", to_model="c", run_dir=run_dir
    )
    assert second.from_model == "b"
    assert second.to_model == "c"


# ---------------------------------------------------------------------------
# marker_path
# ---------------------------------------------------------------------------


def test_marker_path_uses_port_filename(run_dir: Path) -> None:
    assert mk.marker_path(8081, run_dir=run_dir).name == "8081.swap"


# ---------------------------------------------------------------------------
# reconcile_marker
# ---------------------------------------------------------------------------


def test_reconcile_alive_owner(run_dir: Path) -> None:
    # Take a marker; current process is the owner, so it's alive.
    taken = mk.take_marker(
        8081, caller="cli", from_model="a", to_model="b", run_dir=run_dir
    )

    result = mk.reconcile_marker(taken)

    assert result.owner_alive is True
    assert result.marker == taken


def test_reconcile_dead_owner(run_dir: Path) -> None:
    # Build a marker by hand whose llauncher_pid is overwhelmingly unlikely
    # to exist (sentinel above PID_MAX).
    stale = mk.SwapMarker(
        port=8081,
        caller="cli",
        started_at="2026-05-02T14:30:00+00:00",
        llauncher_pid=2**31 - 1,
        from_model="a",
        to_model="b",
    )

    result = mk.reconcile_marker(stale)

    assert result.owner_alive is False
