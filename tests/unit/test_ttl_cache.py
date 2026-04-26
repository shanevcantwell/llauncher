"""Unit tests for ``_TTLCache`` utility (used by model_health & gpu)."""

from __future__ import annotations

import time

import pytest

from llauncher.util.cache import _TTLCache


@pytest.fixture(autouse=True)
def _reset_cache():
    """Create a fresh TTL cache before each test."""
    yield None  # Each test gets its own instance via fixture parameters


# ── Basic get/set ───────────────────────────────────────────────

def test_basic_get_set():
    """Setting a key returns it on a subsequent get()."""
    cache = _TTLCache(ttl_seconds=60)
    cache.set("key", "value")
    assert cache.get("key") == "value"


def test_get_missing_key_returns_none():
    """A key that was never set or has expired returns None."""
    cache = _TTLCache(ttl_seconds=60)
    result = cache.get("nonexistent")
    assert result is None


# ── Expiry ─────────────────────────────────────────────────────

def test_entry_expires_after_ttl():
    """Entry is returned as None once the TTL has passed."""
    cache = _TTLCache(ttl_seconds=1)  # 1-second TTL
    cache.set("key", "value")
    assert cache.get("key") == "value"  # still present

    time.sleep(1.1)  # wait for expiry
    assert cache.get("key") is None


def test_default_ttl_applied():
    """When no per-call TTL is given, the default class-level TTL is used."""
    cache = _TTLCache(ttl_seconds=1)
    cache.set("key", "value")  # uses default ttl_seconds

    time.sleep(1.1)
    assert cache.get("key") is None


# ── Invalidation ────────────────────────────────────────────────

def test_invalidate_all_clears_everything():
    """After invalidate_all(), all entries are gone."""
    cache = _TTLCache(ttl_seconds=3600)
    for i in range(10):
        cache.set(f"k{i}", f"v{i}")

    cache.invalidate_all()
    assert cache.get("k0") is None
    assert cache.get("k5") is None


# ── Separate instances are isolated ────────────────────────────

def test_separate_instances_are_isolated():
    """Two independent _TTLCache instances do not share data."""
    c1 = _TTLCache(ttl_seconds=60)
    c2 = _TTLCache(ttl_seconds=60)

    c1.set("shared_key", "from_c1")
    assert c1.get("shared_key") == "from_c1"
    assert c2.get("shared_key") is None  # not visible in c2


def test_per_call_ttl_override():
    """Per-call TTL overrides the class default."""
    cache = _TTLCache(ttl_seconds=60)  # long default

    cache.set("short", "val", ttl_seconds=1)  # but short for this key
    time.sleep(1.1)
    assert cache.get("short") is None  # expired


def test_different_keys_dont_interfere():
    """Setting key A doesn't affect the expiry of key B."""
    cache = _TTLCache(ttl_seconds=60)

    for i in range(5):
        cache.set(f"key_{i}", f"value_{i}")

    assert all(cache.get(f"key_{i}") == f"value_{i}" for i in range(5))


def test_ttl_cache_zero_ttl():
    """A TTL of 0 effectively means 'expire immediately'.

    This tests the edge case where entries expire before they can be read.
    """
    cache = _TTLCache(ttl_seconds=0)
    cache.set("key", "value")
    # With ttl=0, expiry is at current time, so should expire on next check
    assert cache.get("key") is None
