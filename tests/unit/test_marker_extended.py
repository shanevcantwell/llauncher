"""Extended unit tests for ``llauncher.core.marker`` error paths.

Targets uncovered branches:
- ``take_marker`` cleanup on write failure (lines 116-123)
- ``request_cancel`` OSError during atomic rewrite (lines 188-193)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from llauncher.core import marker as mk


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "run"


def test_take_marker_cleanup_on_write_failure(run_dir: Path) -> None:
    """If json.dump raises mid-write, the partial marker file is unlinked."""
    run_dir.mkdir(parents=True, exist_ok=True)
    path = mk.marker_path(8081, run_dir)

    with patch("llauncher.core.marker.json.dump", side_effect=RuntimeError("disk full")):
        with pytest.raises(RuntimeError):
            mk.take_marker(
                8081,
                caller="cli",
                from_model="a",
                to_model="b",
                run_dir=run_dir,
            )
    # Partial file must not survive.
    assert not path.exists()


def test_request_cancel_oserror_cleans_tempfile_and_raises(run_dir: Path) -> None:
    """OSError during os.replace bubbles up after cleaning the .swap.tmp."""
    # First, place a valid marker we can attempt to mutate.
    mk.take_marker(
        8081,
        caller="cli",
        from_model="a",
        to_model="b",
        run_dir=run_dir,
    )

    with patch("llauncher.core.marker.os.replace", side_effect=OSError("denied")):
        with pytest.raises(OSError):
            mk.request_cancel(8081, run_dir=run_dir)

    # Tempfile must have been cleaned up by the except handler.
    tmp_path = mk.marker_path(8081, run_dir).with_suffix(".swap.tmp")
    assert not tmp_path.exists()


def test_request_cancel_tempfile_unlink_filenotfound_is_ignored(run_dir: Path) -> None:
    """If cleanup unlink raises FileNotFoundError it's swallowed; the OSError still raises."""
    mk.take_marker(
        8081,
        caller="cli",
        from_model="a",
        to_model="b",
        run_dir=run_dir,
    )

    real_unlink = Path.unlink

    def fake_unlink(self, *args, **kwargs):
        if self.name.endswith(".swap.tmp"):
            raise FileNotFoundError
        return real_unlink(self, *args, **kwargs)

    with patch("llauncher.core.marker.os.replace", side_effect=OSError("denied")), \
         patch.object(Path, "unlink", fake_unlink):
        with pytest.raises(OSError):
            mk.request_cancel(8081, run_dir=run_dir)
