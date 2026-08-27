"""Regression tests for ADR-LLNCH-014 cancel verb (issue #54).

The existing ``tests/unit/test_operations.py`` already covers the five
canonical cancel paths (pre-preflight, post-commit advisory, post-stop,
during readiness, post-success). This file pins down the remaining
*error* branches that the feature introduced:

* **Cancel-then-rollback-also-fails** (``swap.py`` lines 649-661): a
  cancel arrives between stop-old and launch-new (post-stop checkpoint)
  AND the cancel-restore launch fails. The port is dead; the action is
  ``failed`` with ``port_state='unavailable'``. Without coverage, a
  refactor of ``_handle_cancel_during_swap`` could silently downgrade
  ``port_state`` to ``restored`` and lie to operators.

* **``take_marker`` partial-write cleanup** (``marker.py`` lines 116-123):
  if writing the marker JSON raises mid-write, the partial file must be
  unlinked. Without it, the empty/corrupt marker poisons subsequent
  reconciliation reads.

* **``request_cancel`` tempfile cleanup** (``marker.py`` lines 188-193):
  if ``os.replace`` fails after writing the temp file, the temp file
  must be unlinked. Without it, ``run_dir`` accumulates ``.swap.tmp``
  detritus.

Together with the existing tests these lock down the full ADR-LLNCH-014
cancel surface — start, swap (all five phases plus failed restore), and
marker module internals.
"""

from __future__ import annotations

import json
import os
import os as _os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llauncher import operations as ops
from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core import marker as mk
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.models.config import ModelConfig


# ---------------------------------------------------------------------------
# Shared fixtures (mirroring tests/unit/test_operations.py — kept local so
# this file can be run in isolation: ``pytest tests/regression``)
# ---------------------------------------------------------------------------


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "run"
    monkeypatch.setattr("llauncher.core.lockfile.LAUNCHER_RUN_DIR", target)
    monkeypatch.setattr("llauncher.core.marker.LAUNCHER_RUN_DIR", target)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_RUN_DIR", target)
    return target


@pytest.fixture
def audit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "audit.jsonl"
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", target)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_AUDIT_PATH", target)
    return target


def _make_config(name: str) -> ModelConfig:
    return ModelConfig.from_dict_unvalidated(
        {
            "name": name,
            "model_path": f"/fake/path/{name}.gguf",
            "n_gpu_layers": 255,
            "ctx_size": 4096,
        }
    )


def _config_lookup(*configs: ModelConfig):
    by_name = {c.name: c for c in configs}

    def side_effect(name: str):
        return by_name.get(name)

    return side_effect


_NO_PREFLIGHT = {"model_health_check": None, "vram_check": None}


# ---------------------------------------------------------------------------
# Cancel during swap post-stop AND cancel-restore fails → port_dead
# ---------------------------------------------------------------------------


def test_swap_cancel_then_rollback_fails_reports_port_dead(
    run_dir: Path, audit_path: Path
) -> None:
    """Cancel at post-stop + failed restore → action='failed', port unavailable.

    Pins ``_handle_cancel_during_swap``'s failed-restore branch
    (``swap.py`` 649-661). A refactor that flips this to ``restored``
    would silently lie about the port state to operators.
    """
    lf.write_lockfile(8081, "old", _os.getpid(), run_dir=run_dir)
    new_popen = MagicMock(); new_popen.pid = 88888

    # Cancel detected at post-stop checkpoint. Restore launch then fails
    # readiness (e.g. the previous_config's model file is gone).
    with patch(
        "llauncher.operations.ConfigStore.get_model",
        side_effect=_config_lookup(_make_config("old"), _make_config("new-model")),
    ), patch("llauncher.operations.proc.stop_server_by_port", return_value=True), \
         patch("llauncher.operations.proc.start_server", return_value=new_popen), \
         patch("llauncher.operations.proc.wait_for_server_ready",
               return_value=(False, ["restore failed: model not found"])), \
         patch("llauncher.operations.proc.stop_server_by_pid"), \
         patch("llauncher.operations.swap.mk.is_cancelled", return_value=True):
        result = ops.swap("new-model", 8081, caller="test", **_NO_PREFLIGHT)

    assert result.success is False
    assert result.action == "failed"
    assert result.port_state == "unavailable"
    assert result.model is None  # port is dead — no model is serving

    # Audit captured the UNAVAILABLE outcome with the diagnostic message.
    entries = al.read_entries(path=audit_path)
    unavailable = [
        e for e in entries
        if e.action == AuditAction.SWAPPED and e.result == AuditResult.UNAVAILABLE
    ]
    assert len(unavailable) >= 1, (
        f"expected SWAPPED/UNAVAILABLE audit entry; got "
        f"{[(e.action, e.result) for e in entries]}"
    )
    assert "port_dead" in unavailable[-1].message

    # Marker released by the swap()-level finally block.
    assert (run_dir / "8081.swap").exists() is False


# ---------------------------------------------------------------------------
# marker.take_marker — partial-write cleanup
# ---------------------------------------------------------------------------


def test_take_marker_cleans_up_partial_write_on_exception(tmp_path: Path) -> None:
    """If JSON dump raises mid-write, the partial marker file must be unlinked.

    Pins ``marker.py`` 116-123. Without this guarantee, the marker
    module's reconciliation would later encounter an empty/corrupt file
    on a port that has no in-flight op.
    """
    run_dir = tmp_path / "run"

    # Make json.dump raise after the file has been created but before
    # any data is written. The except block must unlink the partial file.
    with patch("llauncher.core.marker.json.dump", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            mk.take_marker(
                8081,
                caller="cli",
                from_model="a",
                to_model="b",
                run_dir=run_dir,
            )

    # The partial marker file must have been cleaned up.
    assert not (run_dir / "8081.swap").exists(), (
        "partial marker file leaked; reconciliation would see corrupt state"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "test double unlinks the marker while the write handle is open — "
        "Windows file locking raises WinError 32 before the simulated "
        "OSError; core/marker.py itself closes before cleanup"
    ),
)
def test_take_marker_partial_write_cleanup_tolerates_missing_file(
    tmp_path: Path,
) -> None:
    """Cleanup path swallows FileNotFoundError if the file is already gone.

    Defensive case: another agent (or the OS) could have removed the
    partial file between the write failure and our unlink attempt. The
    cleanup must not mask the original exception with a secondary one.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    def boom_dump(*_a, **_kw):
        # Pre-delete the file the fdopen handle is attached to so the
        # subsequent ``path.unlink()`` raises FileNotFoundError. The
        # except clause should swallow it and re-raise the original.
        for f in run_dir.glob("*.swap"):
            f.unlink()
        raise OSError("simulated write failure")

    with patch("llauncher.core.marker.json.dump", side_effect=boom_dump):
        with pytest.raises(OSError, match="simulated write failure"):
            mk.take_marker(
                8081,
                caller="cli",
                from_model="a",
                to_model="b",
                run_dir=run_dir,
            )

    assert not (run_dir / "8081.swap").exists()


# ---------------------------------------------------------------------------
# marker.request_cancel — tempfile cleanup on os.replace failure
# ---------------------------------------------------------------------------


def test_request_cancel_cleans_up_tempfile_on_replace_failure(
    tmp_path: Path,
) -> None:
    """OSError during ``os.replace`` must trigger tempfile cleanup.

    Pins ``marker.py`` 188-193. Without this, ``run_dir`` accumulates
    ``8081.swap.tmp`` files that confuse later operators / scripts.
    """
    run_dir = tmp_path / "run"
    # Seed an existing marker so ``request_cancel`` proceeds past the
    # "no marker" early return.
    mk.take_marker(
        8081,
        caller="cli",
        from_model="a",
        to_model="b",
        run_dir=run_dir,
    )

    with patch(
        "llauncher.core.marker.os.replace",
        side_effect=OSError("simulated EXDEV cross-device link"),
    ):
        with pytest.raises(OSError, match="simulated EXDEV"):
            mk.request_cancel(8081, run_dir=run_dir)

    # Live marker is unchanged (replace never succeeded).
    live = mk.read_marker(8081, run_dir=run_dir)
    assert live is not None
    assert live.cancelled is False, (
        "marker should be untouched when os.replace failed"
    )

    # Tempfile must have been cleaned up — no .swap.tmp left behind.
    leftover = list(run_dir.glob("*.swap.tmp"))
    assert leftover == [], (
        f"request_cancel leaked tempfiles on os.replace failure: {leftover}"
    )
