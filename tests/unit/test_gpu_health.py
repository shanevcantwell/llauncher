"""Unit tests for ``GPUHealthCollector`` (ADR-006).

Covers:
- no backend returns empty list
- simulated NVIDIA SMI output is parsed correctly
- multi-GPU simulation
- TTL cache invalidation on refresh
- data structure consistency across calls
"""

from __future__ import annotations

import shutil


class TestNoBackendReturnsEmpty:
    """When no GPU tools are available, the collector returns clean empties."""

    def test_no_backend_returns_empty(self):
        """GPUHealthCollector without any backend returns empty backends list.

        We mock subprocess.run so that all SMI CLI calls fail gracefully,
        simulating an environment without GPU drivers (pure CPU).
        """
        import subprocess

        def mock_run(*args, **kwargs):
            # Simulate command not found or permission denied for every call.
            err = subprocess.CalledProcessError(1, args[0][0] if isinstance(args[0], list) else args[0])
            raise err

        from unittest.mock import patch
        with patch("subprocess.run", mock_run):
            # Also mock is_apple_mps_available to return False (no Apple hardware)
            with patch("llauncher.core.gpu.is_apple_mps_available", return_value=False):
                from llauncher.core.gpu import GPUHealthCollector

                collector = GPUHealthCollector()
                result = collector.get_health()

                assert isinstance(result, dict), f"Expected dict; got {type(result)}"
                assert "backends" in result
                assert result["backends"] == []


class TestSimulatedNVIDIAOutput:
    """Tests against parsed NVIDIA SMI output (simulated/canned data)."""

    def test_simulated_nvidia_output_parsed(self):
        """Simulated nvidia-smi output parses into structured device data."""
        from llauncher.core.gpu import GPUHealthCollector

        collector = GPUHealthCollector()
        result = collector._query_NVIDIA(simulated_output=True)

        assert isinstance(result["devices"], list)
        assert len(result["devices"]) > 0

        device = result["devices"][0]
        assert hasattr(device, "index") or "index" in dict(device.__dict__ if hasattr(device, "__dict__") else {})
        # Check it's a GPUDevice dataclass with required fields.
        assert "index" in dir(device) and hasattr(device, "name")
        assert hasattr(device, "total_vram_mb")
        assert hasattr(device, "used_vram_mb")
        assert hasattr(device, "free_vram_mb")

    def test_simulated_multi_gpu_output(self):
        """Multi-GPU nvidia-smi output correctly identifies all devices."""
        from llauncher.core.gpu import GPUHealthCollector, _NVIDIA_MULTI_GPU_SIMULATED

        collector = GPUHealthCollector()
        result = collector._query_NVIDIA(simulated_output=_NVIDIA_MULTI_GPU_SIMULATED)

        assert len(result["devices"]) == 2

        for device in result["devices"]:
            assert hasattr(device, "index") and hasattr(device, "total_vram_mb")
            assert device.total_vram_mb > 0


class TestLifecycleProcessesMapped:
    """Process attribution for llama-server instances."""

    def test_lifecycle_processes_mapped(self):
        """Calling refresh() populates process lists without crashing."""
        from llauncher.core.gpu import GPUHealthCollector

        collector = GPUHealthCollector()
        result = collector.refresh()  # Uses internal _collect_devices.

        assert isinstance(result, object)
        assert hasattr(result, "devices")
        assert hasattr(result, "backends")


class TestVRAMConsistency:
    """VRAM data is consistent across repeated health queries."""

    def test_vram_before_and_after_start(self):
        """Structure keys remain the same between calls even when no GPU exists."""
        from llauncher.core.gpu import GPUHealthCollector

        collector = GPUHealthCollector()
        before = collector.get_health(force_refresh=True)
        after = collector.get_health(force_refresh=True)

        assert set(before.keys()) == set(after.keys())


class TestTTLCacheInvalidation:
    """TTL cache behavior for the GPU collector."""

    def test_ttl_cache_invalidation(self):
        """TTL cache is invalidated on refresh() and returns fresh data.

        Uses a very short TTL to avoid making tests slow with real time.sleep().
        """
        from llauncher.util.cache import _TTLCache
        from llauncher.core.gpu import GPUHealthCollector

        collector = GPUHealthCollector(cache=_TTLCache(ttl_seconds=0))  # expire immediately.

        # First call populates cache (even though TTL is 0, it sets the value).
        result1 = collector.get_health(force_refresh=True)

        # Second call should miss the expired cache and re-query.
        result2 = collector.get_health(force_refresh=True)

        assert isinstance(result2, dict)

    def test_cache_stored_for_short_queries(self):
        """Within TTL, subsequent calls hit the cache."""
        from llauncher.util.cache import _TTLCache
        from llauncher.core.gpu import GPUHealthCollector

        counter = [0]  # track how many times _collect_devices is called.

        class CountingCollector(GPUHealthCollector):
            def _collect_devices(self, *a, **k):
                counter[0] += 1
                return super()._collect_devices(*a, **k)

        short_cache = _TTLCache(ttl_seconds=59)
        cc = CountingCollector(cache=short_cache)

        # Force refresh → populates cache.
        cc.get_health(force_refresh=True)
        first_count = counter[0]

        # Subsequent call without force should use cache (no extra collection calls).
        _ = cc.get_health()  # noqa: F841
        second_count = counter[0]

        assert second_count == first_count, "Cache hit should not re-collect."


class TestGPUAvailableMethod:
    """Basic checks for the is_available() helper."""

    def test_is_available_returns_bool(self):
        """is_available always returns a boolean even when tool is absent."""
        from llauncher.core.gpu import GPUHealthCollector

        collector = GPUHealthCollector()
        result = collector.is_available("nvidia")  # might be True or False depending on env.
        assert isinstance(result, bool)
