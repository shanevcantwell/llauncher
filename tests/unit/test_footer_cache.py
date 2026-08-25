"""Unit tests for the footer-context cache (ADR-LLNCH-012).

Covers the cache behavior in isolation from the HTTP layer:
read-through, TTL expiry, cache-disable (TTL <= 0), missing-lockfile
(None passthrough), missing-config (degraded null fields), and
absence-not-cached invariant.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest

from llauncher.agent import footer_cache


# ─── Fakes ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _FakeLockfile:
    port: int
    model: str
    pid: int = 12345
    started_at: str = "2026-05-15T00:00:00+00:00"
    llauncher_pid: int = 1


@dataclass(frozen=True)
class _FakeModelConfig:
    ctx_size: int = 131072
    parallel: int = 4


class _Counter:
    """Counts how many times the patched callable was invoked."""

    def __init__(self, return_value: Any) -> None:
        self.return_value = return_value
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if callable(self.return_value):
            return self.return_value(*args, **kwargs)
        return self.return_value


# ─── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_cache():
    footer_cache.clear_cache()
    yield
    footer_cache.clear_cache()


@pytest.fixture
def patch_disk(monkeypatch):
    """Patch read_lockfile and ConfigStore.get_model with counted fakes."""

    def _apply(
        *,
        lockfile: _FakeLockfile | None,
        config: _FakeModelConfig | None,
    ) -> tuple[_Counter, _Counter]:
        lf_counter = _Counter(lockfile)
        cfg_counter = _Counter(config)
        monkeypatch.setattr(footer_cache, "read_lockfile", lf_counter)
        # ConfigStore is referenced as a class; patch get_model on it.
        monkeypatch.setattr(
            footer_cache.ConfigStore, "get_model", classmethod(lambda cls, name: cfg_counter(name))
        )
        return lf_counter, cfg_counter

    return _apply


# ─── Tests ─────────────────────────────────────────────────────────


class TestReadThrough:
    def test_returns_full_context_when_lockfile_and_config_present(self, patch_disk):
        patch_disk(
            lockfile=_FakeLockfile(port=8081, model="qwen"),
            config=_FakeModelConfig(ctx_size=32768, parallel=2),
        )
        ctx = footer_cache.get_footer_context(8081)
        assert ctx is not None
        assert ctx.port == 8081
        assert ctx.model == "qwen"
        assert ctx.ctx_size == 32768
        assert ctx.parallel == 2

    def test_returns_none_when_lockfile_absent(self, patch_disk):
        patch_disk(lockfile=None, config=None)
        assert footer_cache.get_footer_context(9999) is None

    def test_returns_degraded_context_when_config_missing(self, patch_disk):
        patch_disk(
            lockfile=_FakeLockfile(port=8081, model="ghost-model"),
            config=None,
        )
        ctx = footer_cache.get_footer_context(8081)
        assert ctx is not None
        assert ctx.model == "ghost-model"
        assert ctx.ctx_size is None
        assert ctx.parallel is None

    def test_to_dict_shape_matches_adr_contract(self, patch_disk):
        patch_disk(
            lockfile=_FakeLockfile(port=8081, model="qwen"),
            config=_FakeModelConfig(ctx_size=4096, parallel=1),
        )
        d = footer_cache.get_footer_context(8081).to_dict()
        assert set(d.keys()) == {"port", "model", "ctx_size", "parallel"}


class TestCaching:
    def test_second_hit_within_ttl_does_not_touch_disk(self, monkeypatch, patch_disk):
        monkeypatch.setattr(footer_cache.settings, "LAUNCHER_FOOTER_CACHE_S", 5.0)
        lf, cfg = patch_disk(
            lockfile=_FakeLockfile(port=8081, model="qwen"),
            config=_FakeModelConfig(),
        )
        footer_cache.get_footer_context(8081)
        footer_cache.get_footer_context(8081)
        footer_cache.get_footer_context(8081)
        assert lf.calls == 1
        assert cfg.calls == 1

    def test_hit_after_ttl_expiry_rereads(self, monkeypatch, patch_disk):
        monkeypatch.setattr(footer_cache.settings, "LAUNCHER_FOOTER_CACHE_S", 0.05)
        lf, _ = patch_disk(
            lockfile=_FakeLockfile(port=8081, model="qwen"),
            config=_FakeModelConfig(),
        )
        footer_cache.get_footer_context(8081)
        time.sleep(0.08)
        footer_cache.get_footer_context(8081)
        assert lf.calls == 2

    def test_ttl_zero_disables_cache(self, monkeypatch, patch_disk):
        monkeypatch.setattr(footer_cache.settings, "LAUNCHER_FOOTER_CACHE_S", 0.0)
        lf, _ = patch_disk(
            lockfile=_FakeLockfile(port=8081, model="qwen"),
            config=_FakeModelConfig(),
        )
        for _ in range(3):
            footer_cache.get_footer_context(8081)
        assert lf.calls == 3

    def test_absence_is_not_cached(self, monkeypatch, patch_disk):
        """A subsequent appearance of a lockfile must not be hidden by a cached None."""
        monkeypatch.setattr(footer_cache.settings, "LAUNCHER_FOOTER_CACHE_S", 5.0)

        # First two calls: no lockfile. Switch the return mid-test by
        # mutating the counter's return_value.
        lf = _Counter(None)
        cfg = _Counter(_FakeModelConfig())
        monkeypatch.setattr(footer_cache, "read_lockfile", lf)
        monkeypatch.setattr(
            footer_cache.ConfigStore, "get_model", classmethod(lambda cls, name: cfg(name))
        )

        assert footer_cache.get_footer_context(8081) is None
        # Lockfile appears now
        lf.return_value = _FakeLockfile(port=8081, model="qwen")
        ctx = footer_cache.get_footer_context(8081)
        assert ctx is not None
        assert ctx.model == "qwen"

    def test_per_port_isolation(self, monkeypatch, patch_disk):
        monkeypatch.setattr(footer_cache.settings, "LAUNCHER_FOOTER_CACHE_S", 5.0)

        def by_port(port: int, **_):
            return _FakeLockfile(port=port, model=f"m{port}")

        lf = _Counter(by_port)
        cfg = _Counter(_FakeModelConfig())
        monkeypatch.setattr(footer_cache, "read_lockfile", lf)
        monkeypatch.setattr(
            footer_cache.ConfigStore, "get_model", classmethod(lambda cls, name: cfg(name))
        )

        a = footer_cache.get_footer_context(8081)
        b = footer_cache.get_footer_context(8082)
        assert a.model == "m8081"
        assert b.model == "m8082"
        assert lf.calls == 2

    def test_clear_cache_forces_reread(self, monkeypatch, patch_disk):
        monkeypatch.setattr(footer_cache.settings, "LAUNCHER_FOOTER_CACHE_S", 5.0)
        lf, _ = patch_disk(
            lockfile=_FakeLockfile(port=8081, model="qwen"),
            config=_FakeModelConfig(),
        )
        footer_cache.get_footer_context(8081)
        footer_cache.clear_cache()
        footer_cache.get_footer_context(8081)
        assert lf.calls == 2
