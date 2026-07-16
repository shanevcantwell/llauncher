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

    Isolation contract (issue #287). ``Path.stat`` is patched at the *class*
    level (the only interception point ``pathlib`` exposes), so the side effect
    must be scoped to *this* test's target path — otherwise it silently governs
    every ``Path`` operation that happens to run inside the patch window,
    including pytest-internal ones on unrelated paths. Two guards enforce that
    scoping and make the pass order-independent:

    1.  **Path-scoped.** The ``OSError`` fires only when ``self`` *is* the target
        ``p``. Any other path (a temp dir, a fixture path, an ``importlib``
        probe from a neighbouring test) falls straight through to the real
        ``stat``. This is what removes the global blast radius that made the
        old arg-shape-only heuristic order-dependent.
    2.  **Probe-preserving.** Even for ``p`` we must let the existence check
        succeed and fail *only* on the explicit size lookup. ``Path.exists``
        calls ``self.stat(follow_symlinks=...)`` (kwargged), whereas the
        rotation code and ``Path.is_file`` both call a bare ``self.stat()``.
        The rotation flow on ``p`` is ``exists()`` then a bare ``stat()`` and
        never ``is_file()``, so gating the raise on "bare call, no kwargs"
        targets exactly the size lookup while the existence probe passes.

    The bare ``OSError`` deliberately carries no ``errno``: ``pathlib``'s
    ``_ignore_error`` would swallow ``ENOENT``/``EBADF`` inside ``exists`` and
    hide the failure, so an errno-less error is what actually propagates to the
    rotation code's ``except OSError`` guard.
    """
    p = tmp_path / "foo.log"
    _big_file(p, 100)

    real_stat = Path.stat

    def selective_stat(self, *args, **kwargs):
        if self == p and not args and not kwargs:
            raise OSError("boom")
        return real_stat(self, *args, **kwargs)

    with patch.object(Path, "stat", selective_stat):
        assert lr.rotate_if_needed(p, max_bytes=10, keep=3) is False
    # The live file must still exist (rotation was aborted).
    assert p.exists()


def test_stat_oserror_patch_is_path_scoped(tmp_path: Path) -> None:
    """Regression guard for issue #287: the size-check ``stat`` failure is
    scoped to the target path and never leaks to unrelated ``Path`` operations.

    The previous ``selective_stat`` discriminated purely by call shape
    (``not args and not kwargs``), so its ``OSError`` fired for *any* path a
    bare ``stat()`` was called on inside the patch window — including the
    identically-shaped call that ``Path.is_file()`` makes. That global blast
    radius is exactly what turned the test order-dependent: whether an unrelated
    path was stat'd during the window decided the outcome. This asserts the two
    behaviours that must hold simultaneously for the isolation to be real:

    * a *bare* ``stat()`` on the target path raises (the branch under test), and
    * the same bare-``stat`` shape on a *different* path (here via
      ``is_file()``) is untouched and returns normally.
    """
    target = tmp_path / "foo.log"
    _big_file(target, 100)
    sibling = tmp_path / "bar.log"
    _big_file(sibling, 100)

    real_stat = Path.stat

    def selective_stat(self, *args, **kwargs):
        if self == target and not args and not kwargs:
            raise OSError("boom")
        return real_stat(self, *args, **kwargs)

    with patch.object(Path, "stat", selective_stat):
        # The target's bare size-check stat raises (rotation aborts).
        with pytest.raises(OSError):
            target.stat()
        # A sibling path — bare-stat via is_file() — is unaffected. If the
        # patch were arg-shape-only (the #287 bug) this would raise too.
        assert sibling.is_file() is True
        assert sibling.stat().st_size == 100


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
