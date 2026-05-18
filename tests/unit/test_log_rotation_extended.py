"""Extended unit tests for ``llauncher.core.log_rotation`` error paths.

Targets uncovered branches:
- ``rotate_if_needed`` stat() OSError → returns False (lines 74-76)
- Oldest-slot unlink OSError aborts cleanly (lines 102-108)
- ``keep=0`` live-file unlink OSError → returns False (lines 127-129)
- Live-file rename OSError → returns False (lines 134-141)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from llauncher.core import log_rotation as lr


def _big_file(p: Path, size: int = 100) -> None:
    p.write_bytes(b"x" * size)


def test_stat_oserror_returns_false(tmp_path: Path) -> None:
    """If ``path.stat()`` (for size check) raises, rotation aborts and returns False.

    ``Path.exists()`` itself calls ``stat`` internally, so the patch must
    let the existence check succeed and only fail on the explicit
    ``path.stat()`` size lookup. We do that by side-effecting only when
    ``follow_symlinks`` is unset (the rotation code calls ``path.stat()``
    with no kwargs; ``Path.exists`` passes ``follow_symlinks``).
    """
    p = tmp_path / "foo.log"
    _big_file(p, 100)

    real_stat = Path.stat

    def selective_stat(self, *args, **kwargs):
        if not args and not kwargs:
            raise OSError("boom")
        return real_stat(self, *args, **kwargs)

    with patch.object(Path, "stat", selective_stat):
        assert lr.rotate_if_needed(p, max_bytes=10, keep=3) is False
    # The live file must still exist (rotation was aborted).
    assert p.exists()


def test_oldest_slot_unlink_failure_aborts(tmp_path: Path) -> None:
    """When unlinking the oldest rotated slot fails, return False without moving live."""
    p = tmp_path / "foo.log"
    _big_file(p, 100)
    # Pre-create the oldest slot to force the unlink branch.
    (tmp_path / "foo.log.3").write_text("old")

    original_unlink = Path.unlink

    def fake_unlink(self, *args, **kwargs):
        if self.name == "foo.log.3":
            raise OSError("perm denied")
        return original_unlink(self, *args, **kwargs)

    with patch.object(Path, "unlink", fake_unlink):
        assert lr.rotate_if_needed(p, max_bytes=10, keep=3) is False
    # Live file is left in place.
    assert p.exists()


def test_keep_zero_live_unlink_failure_returns_false(tmp_path: Path) -> None:
    """With keep=0, if live-file unlink raises OSError, return False."""
    p = tmp_path / "foo.log"
    _big_file(p, 100)

    def fake_unlink(self, *args, **kwargs):
        raise OSError("denied")

    with patch.object(Path, "unlink", fake_unlink):
        assert lr.rotate_if_needed(p, max_bytes=10, keep=0) is False


def test_live_rename_failure_returns_false(tmp_path: Path) -> None:
    """If os.replace(live → .1) raises OSError, abort rotation cleanly."""
    p = tmp_path / "foo.log"
    _big_file(p, 100)

    real_replace = lr.os.replace

    def fake_replace(src, dst):
        # Fail only for the live → .1 move.
        if str(src).endswith("foo.log") and str(dst).endswith("foo.log.1"):
            raise OSError("rename failed")
        return real_replace(src, dst)

    with patch.object(lr.os, "replace", fake_replace):
        assert lr.rotate_if_needed(p, max_bytes=10, keep=3) is False
    # Live file is left in place.
    assert p.exists()
