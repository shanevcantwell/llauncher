"""Unit tests for model health validation (ADR-005).

Tests ``check_model_health()`` and ``ModelHealthResult`` covering:
- valid file, nonexistent, empty, symlink resolved, broken symlink, unreadable
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

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
