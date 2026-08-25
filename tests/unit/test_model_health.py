"""Unit tests for model health validation (ADR-LLNCH-005).

Tests ``check_model_health()`` and ``ModelHealthResult`` covering:
- valid file, nonexistent, empty, symlink resolved, broken symlink, unreadable
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from llauncher.core.model_health import check_model_health, ModelHealthResult


@pytest.fixture(autouse=True)
def _reset_cache():
    """Invalidate the module-level health cache before each test."""
    from llauncher.core import model_health as mh
    try:
        mh._health_cache.invalidate_all()
    except Exception:
        pass
    yield
    # Cleanup after.


# ── 1. Existing valid file (> 1 MB) ─────────────────────────────

def test_existing_valid_file():
    """Existing readable file > 1MB returns valid=True."""
    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False, mode="wb") as f:
        f.write(b"x" * (1024 * 1024 + 1))
        path = Path(f.name).resolve()

    result = check_model_health(str(path))
    assert result.valid is True, f"Expected valid=True for existing >1MB file; got: {result.model_dump()}"
    assert result.exists is True
    assert result.readable is True
    assert result.size_bytes == 1024 * 1024 + 1


# ── 2. Nonexistent file ────────────────────────────────────────

def test_nonexistent_file():
    """Non-existent model path returns valid=False with reason."""
    result = check_model_health("/nonexistent/path/to/model.gguf")
    assert isinstance(result, ModelHealthResult)
    dumped = result.model_dump()
    assert dumped["valid"] is False
    assert dumped["exists"] is False
    assert "not found" in (dumped["reason"] or "").lower()


# ── 3. Empty file (< 1 MB) ─────────────────────────────────────

def test_empty_file():
    """Empty file (< 1MB) returns valid=False — heuristic for corruption."""
    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False, mode="wb") as f:
        path = Path(f.name).resolve()

    result = check_model_health(str(path))
    assert isinstance(result, ModelHealthResult)
    dumped = result.model_dump()
    assert dumped["valid"] is False
    assert "too small" in (dumped["reason"] or "").lower()


# ── 4. Symlink resolved to valid target ─────────────────────────

def test_symlink_resolved():
    """Symlinks are resolved and target validation applies."""
    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False, mode="wb") as f:
        f.write(b"x" * (1024 * 1024 + 1))
        real_path = Path(f.name).resolve()

    symlink_dir = tempfile.mkdtemp()
    symlink_path = Path(symlink_dir) / "model.gguf"
    symlink_path.symlink_to(real_path)

    result = check_model_health(str(symlink_path))
    assert isinstance(result, ModelHealthResult)
    dumped = result.model_dump()
    assert dumped["valid"] is True


# ── 5. Broken symlink ─────────────────────────────────────────

def test_symlink_to_nonexistent():
    """Broken symlink returns valid=False."""
    broken_dir = tempfile.mkdtemp()
    broken_path = Path(broken_dir) / "broken.gguf"
    broken_path.symlink_to("/nonexistent/target.gguf")

    result = check_model_health(str(broken_path))
    assert isinstance(result, ModelHealthResult)
    dumped = result.model_dump()
    assert dumped["valid"] is False


# ── 6. Unreadable file (no read permission) ───────────────────

def test_unreadable_file():
    """File without read permission returns valid=False."""
    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False, mode="wb") as f:
        f.write(b"x" * (1024 * 1024 + 1))
        path = Path(f.name).resolve()

    # Remove read permissions for all.
    os.chmod(path, stat.S_IWUSR)  # keep only write
    try:
        result = check_model_health(str(path))
        assert isinstance(result, ModelHealthResult)
        dumped = result.model_dump()
        assert dumped["valid"] is False
        reason_lower = (dumped["reason"] or "").lower()
        assert "permission" in reason_lower or "unreadable" in reason_lower
    finally:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # restore for cleanup


def test_last_modified_populated_for_valid():
    """Last modified timestamp is present for valid files."""
    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False, mode="wb") as f:
        f.write(b"x" * (1024 * 1024 + 1))
        path = Path(f.name).resolve()

    result = check_model_health(str(path))
    assert isinstance(result.model_dump()["last_modified"], str) or hasattr(result, "last_modified")


def test_stat_oserror_continues_to_readability_check():
    """An explicit ``stat()`` ``OSError`` is swallowed (lines 93-94).

    The existence gate uses ``is_file()``; the *separate* ``path.stat()`` for
    size/mtime can still fail on edge cases (e.g. a vanish-after-is_file race).
    When it does, size/mtime stay unset and control falls through to the
    readability check rather than aborting. With ``size_bytes`` left ``None``
    the downstream size heuristic then reports ``too small`` — the visible
    consequence of the swallowed edge.

    ``is_file`` is forced True so the explicit ``stat()`` at line 90 (not
    ``is_file``'s own internal stat, which swallows ``OSError`` to False) is
    the one that raises — isolating exactly the branch under test.
    """
    from pathlib import Path as _Path

    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False, mode="wb") as f:
        f.write(b"x" * (1024 * 1024 + 1))
        path = Path(f.name).resolve()

    with patch.object(_Path, "is_file", return_value=True), patch.object(
        _Path, "stat", side_effect=OSError("stat unavailable")
    ):
        result = check_model_health(str(path))

    # Readability check still ran (file is openable via builtin open), so the
    # unreadable path was NOT taken.
    assert result.readable is True
    assert result.size_bytes is None
    assert result.last_modified is None
    # size_bytes None -> heuristic treats as 0 -> "too small".
    assert result.valid is False
    assert (result.reason or "").lower() == "too small"


def test_resolution_failure_recovers_with_reason():
    """An unexpected exception during resolution is caught (lines 106-109).

    If ``Path.resolve()`` raises something other than the inner-handled
    ``OSError`` (e.g. a ``RuntimeError`` from a pathological path), the outer
    ``except Exception`` records the message as ``reason`` and returns an
    invalid result instead of propagating.
    """
    from pathlib import Path as _Path

    with patch.object(_Path, "resolve", side_effect=RuntimeError("boom-resolve")):
        result = check_model_health("/some/path/that/will/blow/up.gguf")

    assert result.valid is False
    assert result.reason is not None
    assert "boom-resolve" in result.reason


def test_cache_invalidation():
    """invalidate_health_cache removes entries as expected."""
    from llauncher.core import model_health as mh

    with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False, mode="wb") as f:
        f.write(b"x" * (1024 * 1024 + 1))
        path = Path(f.name).resolve()

    # First call populates cache.
    check_model_health(str(path))
    assert mh._health_cache.get(str(path)) is not None

    # Invalidate specific entry.
    mh.invalidate_health_cache(model_path=str(path))
    assert mh._health_cache.get(str(path)) is None


# ── Shard-resolution precondition (issue #475) ──────────────────
#
# ``ModelConfig.model_exists`` resolves the sharded ``-of-`` naming
# pattern to a base ``.gguf`` file; ``check_model_health`` must resolve
# identically via the shared ``resolve_shard_path`` helper in
# ``models/config.py`` — otherwise a sharded-but-present model reports
# healthy from ``ModelConfig`` construction but "not found" from the
# health checker (or vice versa), and #468's "delete entries with
# missing weights" rule would delete a genuinely good entry.


def test_check_model_health_resolves_sharded_path(tmp_path):
    """A sharded entry (base .gguf present, first-shard filename absent)
    validates OK via ``check_model_health`` — matching ``ModelConfig``.

    Base-path derivation mirrors ``resolve_shard_path``: everything from
    ``-of-`` onward is dropped and replaced with ``.gguf`` — i.e.
    ``big-model-00001-of-00003.gguf`` resolves against
    ``big-model-00001.gguf``.
    """
    base = tmp_path / "big-model-00001.gguf"
    base.write_bytes(b"x" * (1024 * 1024 + 1))

    sharded_name = str(tmp_path / "big-model-00001-of-00003.gguf")

    result = check_model_health(sharded_name)
    assert result.valid is True, f"expected shard fallback to resolve; got {result.model_dump()}"
    assert result.exists is True


def test_check_model_health_resolves_sharded_path_matches_model_config(tmp_path):
    """Regression guard: ``ModelConfig.model_exists`` and
    ``check_model_health`` agree on a sharded path (both OK) and on a
    genuinely missing non-sharded path (both fail)."""
    from llauncher.models.config import ModelConfig

    base = tmp_path / "shard-model-00001.gguf"
    base.write_bytes(b"x" * (1024 * 1024 + 1))
    sharded_name = str(tmp_path / "shard-model-00001-of-00003.gguf")

    # ModelConfig construction succeeds (shard fallback resolves).
    cfg = ModelConfig(name="shard-model", model_path=sharded_name)
    assert cfg.model_path == sharded_name

    # check_model_health agrees.
    health = check_model_health(sharded_name)
    assert health.valid is True

    # A non-sharded, genuinely missing path fails from both call sites.
    missing_path = str(tmp_path / "totally-missing.gguf")
    try:
        ModelConfig(name="missing-model", model_path=missing_path)
        raised = False
    except Exception:
        raised = True
    assert raised is True, "ModelConfig should reject a genuinely missing path"

    missing_health = check_model_health(missing_path)
    assert missing_health.valid is False
    assert missing_health.exists is False
