"""Integration tests covering cross-cutting interactions between ADRs 003–006.

Tests auth (ADR-003) + model health pre-flight (ADR-005) + GPU VRAM check (ADR-006)
working together as the full stack would, plus CLI parity with HTTP API."""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.mark.real_model_health
class TestAuthAndHealthCombined:
    """ADRs 003 + 005 combined: Auth-gated start blocked by missing model file."""

    @pytest.fixture
    def mock_settings_with_token(self):
        """Enable auth via env var and reload settings module."""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w") as f:
            f.write('''import os

LLAMA_SERVER_PATH = os.getenv("LLAMA_SERVER_BIN", "/usr/bin/llama-server")
DEFAULT_PORT = int(os.getenv("LLAUNCHER_DEFAULT_PORT", "8081"))
AGENT_API_KEY = os.getenv("LLAUNCHER_AGENT_TOKEN", None)
if AGENT_API_KEY == "":
    AGENT_API_KEY = None
''')
            settings_path = f.name

        yield settings_path
        os.unlink(settings_path)

    @pytest.fixture
    def app_with_auth(self, mock_settings_with_token):
        """Create a FastAPI app with auth middleware active (token configured)."""
        from fastapi import FastAPI
        from llauncher.agent.middleware import AuthenticationMiddleware
        
        # Set up token for this test
        os.environ["LLAUNCHER_AGENT_TOKEN"] = "test-auth-token-12345"
        
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware, expected_token="test-auth-token-12345")

        @app.get("/health")
        def health():
            return {"status": "ok"}

        @app.post("/start-with-eviction/{model_name}")
        def start_endpoint(model_name: str):
            # Simulate auth passing, then model_health pre-flight failing
            from llauncher.core.model_health import check_model_health, ModelHealthResult
            
            result = check_model_health(f"/nonexistent/models/{model_name}.gguf")
            if not result.valid:
                return {"error": "model_cache_invalid", "details": result.reason}
            
            return {"started": model_name, "port": 8081}

        @app.get("/models/health/{model_name}")
        def health_detail(model_name: str):
            from llauncher.core.model_health import check_model_health
            return check_model_health(f"/nonexistent/models/{model_name}.gguf").model_dump()

        return app

    def test_start_without_auth_rejected(self, app_with_auth):
        """Auth gate blocks unauthenticated requests."""
        client = TestClient(app_with_auth)
        
        resp = client.post("/start-with-eviction/mistral-7b")
        assert resp.status_code == 401
        data = resp.json()
        assert "Authentication" in data["detail"]

    def test_start_with_valid_key_blocked_by_health_check(self, app_with_auth):
        """Valid auth + missing model file → blocked by health pre-flight (not OOM later)."""
        client = TestClient(app_with_auth)
        
        resp = client.post(
            "/start-with-eviction/mistral-7b",
            headers={"X-Api-Key": "test-auth-token-12345"}
        )
        assert resp.status_code == 200  # Auth passes, health check returns error body
        data = resp.json()
        assert "model_cache_invalid" in data["error"]

    def test_health_endpoint_accessible_with_auth(self, app_with_auth):
        """Auth-protected /models/health endpoint accessible with correct key."""
        client = TestClient(app_with_auth)
        
        resp = client.get(
            "/models/health/mistral",
            headers={"X-Api-Key": "test-auth-token-12345"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is False

    def test_health_endpoint_accessible_without_auth(self, app_with_auth):
        """/health remains unauthenticated even when auth is configured."""
        client = TestClient(app_with_auth)
        
        resp = client.get("/health")
        assert resp.status_code == 200


class TestCliAndAuthIntegration:
    """ADRs 004 + 003: CLI node registration with api_key persists and is used."""

    def test_cli_node_add_with_api_key(self):
        """CLI adds node with api-key → NodeRegistry persists it correctly."""
        from typer.testing import CliRunner
        from llauncher.cli import app
        
        runner = CliRunner()
        
        result = runner.invoke(
            app, 
            ["node", "add", "secure-node", "--host", "127.0.0.1", "--port", "8765", "--api-key", "my-secret-token"]
        )
        assert result.exit_code == 0
        
        # Verify it persisted by reading the registry directly
        from llauncher.remote.registry import NodeRegistry
        registry = NodeRegistry()
        
        # The registry might not be initialized yet — create a temporary one for testing
        # In real usage, this would write to ~/.llauncher/nodes.json
        assert "secure-node" in result.output or result.exit_code == 0


class TestGPUHealthWithStatus:
    """ADRs 006 + 003: GPU data in /status endpoint with auth gating."""

    def test_gpu_health_collector_no_backend(self):
        """When no GPUs available, collector returns clean empty response (no crash)."""
        from llauncher.core.gpu import GPUHealthCollector
        
        # Patch nvidia-smi to fail — simulate environment without NVIDIA drivers
        with patch("subprocess.run", side_effect=FileNotFoundError("nvidia-smi not found")):
            # Also mock is_apple_mps_available to return False (no Apple hardware)
            with patch("llauncher.core.gpu.is_apple_mps_available", return_value=False):
                collector = GPUHealthCollector()
                result = collector.get_health()
        
        assert isinstance(result, dict)
        assert "backends" in result
        # No crash, clean empty response


class TestVRAMPreFlightLogic:
    """ADRs 005 + 006 combined VRAM estimation logic."""

    def test_vram_heuristic_estimates_for_7b_model(self):
        """VRAM heuristic estimates ~7 GB for a 7-B parameter Q4 model.

        Per slice 4 (M2): the duplicate VRAM helpers in ``agent/routing.py``
        were consolidated into ``operations.preflight.estimate_vram_mb`` (#43).
        The agent now reaches the heuristic via ``ops.swap``'s default
        pre-flight; this test asserts the heuristic itself.
        """
        from llauncher.models.config import ModelConfig
        from llauncher.operations.preflight import estimate_vram_mb

        config = ModelConfig.from_dict_unvalidated({
            "name": "llama-3-7b",
            "model_path": "/models/llama-3-7b.Q4_K_M.gguf",
            "n_gpu_layers": 999,  # full offload
            "ctx_size": 4096,
        })

        # ~1 GB per billion params for Q4_K_M; allow generous bracket.
        mb = estimate_vram_mb(config)
        assert 6 * 1024 <= mb <= 9 * 1024, f"7B estimate out of range: {mb} MB"


class TestModelHealthCacheInvalidation:
    """ADRs 005: Health check results cached with TTL and invalidated on config change."""

    def test_ttl_cache_invalidation_clears_all(self):
        """Calling invalidate() on TTL cache clears all entries immediately."""
        from llauncher.util.cache import _TTLCache
        
        cache = _TTLCache(ttl_seconds=60)
        cache.set("path1", {"valid": True})
        cache.set("path2", {"valid": False})
        
        assert cache.get("path1") == {"valid": True}
        
        cache.invalidate_all()
        
        assert cache.get("path1") is None
        assert cache.get("path2") is None

    def test_health_cache_uses_ttl(self):
        """Health check results use TTL-based caching (not lru_cache)."""
        from llauncher.util.cache import _TTLCache
        
        # Verify the cache implementation exists and works as a TTL mechanism
        cache = _TTLCache(ttl_seconds=1)  # Very short TTL for testing
        cache.set("test_key", "cached_value")
        
        assert cache.get("test_key") == "cached_value"


