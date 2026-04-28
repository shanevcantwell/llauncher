# Brief: Test Quality Remediation (Worker)

**Source Review:** plan-sleeptime-remediation-00-review-opus-4.7-complete-review.md, pr-test-analyzer agent output  
**Related Coordinator:** brief-python-reviewer.md, brief-silent-failure-hunter.md (test files reference code under modification — changes must be coordinated)  
**Scope Limitation:** Only modify test files. No production code changes in this brief.

---

## Objective

Rewrite all weak/tautological tests to actually verify behavioral outcomes rather than cosmetic assertions. Add missing gap-tests for behaviors claimed by ADRs but untested. Produce a test suite that would reliably catch regressions from the remediation fixes in other briefs.

---

## Per-File Remediation Plan

### 1. `tests/unit/test_core_settings_auth.py` — WEAK → STRONG

**Current state:** Three tests, all testing that `settings.AGENT_API_KEY` mirrors the env var. Fragile use of `importlib.reload()`. No edge cases covered.

**Replace all three existing tests with:**

```python
def test_api_key_from_env(self):
    """Normal case: token set in env → AGENT_API_KEY is non-empty string."""
    os.environ["LAUNCHER_AGENT_TOKEN"] = "test-token-value"
    import importlib; import llauncher.core.settings as s; importlib.reload(s)
    assert s.AGENT_API_KEY == "test-token-value"

def test_api_key_whitespace_only(self):
    """Whitespace-only token should be treated the same as a real token 
    (no normalization is implemented — this documents current behavior)."""
    os.environ["LAUNCHER_AGENT_TOKEN"] = "   \t\n  "
    import importlib; import llauncher.core.settings as s; importlib.reload(s)
    # Current behavior: whitespace IS the key. This is a known limitation, tested here.
    assert s.AGENT_API_KEY == "   \t\n  "

def test_api_key_very_long(self):
    """Very long token should not crash or cause performance issues."""
    os.environ["LAUNCHER_AGENT_TOKEN"] = "a" * 10000
    import importlib; import llauncher.core.settings as s; importlib.reload(s)
    assert len(s.AGENT_API_KEY) == 10000

def test_api_key_unset_returns_none(self):
    """When env var is absent, AGENT_API_KEY should be None (not empty string)."""
    if "LAUNCHER_AGENT_TOKEN" in os.environ:
        del os.environ["LAUNCHER_AGENT_TOKEN"]
    import importlib; import llauncher.core.settings as s; importlib.reload(s)
    assert s.AGENT_API_KEY is None

def test_token_removed_after_set(self):
    """Unsetting the env var after setting it should result in None on reload."""
    os.environ["LAUNCHER_AGENT_TOKEN"] = "temp"
    import importlib; import llauncher.core.settings as s; importlib.reload(s)
    assert s.AGENT_API_KEY == "temp"
    del os.environ["LAUNCHER_AGENT_TOKEN"]
    importlib.reload(s)
    assert s.AGENT_API_KEY is None
```

### 2. `tests/unit/test_remote_node_auth.py` — WEAK/TAUTOLOGICAL → ADEQUATE

**Current state:** Lines 10+ patch HTTP layer then call `_get_headers()` directly, bypassing the mock. Proves nothing about actual HTTP calls.

**Replace with integration-style tests using a real httpx.TestClient:**

```python
class TestRemoteNodeHTTPHeaders:
    """Verify X-Api-Key header is actually transmitted over HTTP, not just constructed."""
    
    def test_api_key_header_present_on_ping(self):
        """A ping() call should include the correct X-Api-Key header."""
        node = RemoteNode("test-node", "http://127.0.0.1:9999", api_key="secret-key")
        headers = node._get_headers()
        assert headers["X-Api-Key"] == "secret-key"
    
    def test_no_api_key_header_when_none(self):
        """Nodes without an API key should not include X-Api-Key header."""
        node = RemoteNode("test-node", "http://127.0.0.1:9999")
        headers = node._get_headers()
        assert "X-Api-Key" not in headers
    
    def test_node_without_key_is_truly_none(self):
        """api_key=None should produce has_api_key=False."""
        node = RemoteNode("test-node", "http://127.0.0.1:9999")
        d = node.to_dict()
        assert d.get("has_api_key") is False
        assert "api_key" not in d or d["api_key"] is None
```

**Note:** Full wire-level HTTP verification (mocking a real server and asserting headers on the network) is deferred to integration tests due to complexity. These unit tests verify the header construction contract.

### 3. `tests/unit/test_gpu_health.py` — WEAK → ADEQUATE + Add Gap Tests

**Remove/replace these specific bad assertions:**

- Line ~84: `assert isinstance(result, object)` → Delete (tautology)
- Line ~103: VRAM consistency test with no value assertion → Replace with actual dict-key comparison including values
- Line ~157: `is_available` return-type-only test → Add assertion about the command string being checked

**Add missing backend coverage tests:**

```python
class TestQueryROCMParsing:
    """_query_ROCM correctly parses rocm-smi output format."""
    
    def test_rocm_parses_device_table(self):
        """Parse standard rocm-smi --showmeminfo memory output into GPUDevice objects."""
        mock_output = '''GPU-Mem-Usage\n  512 MiB / 8192 MiB'''
        # ... setup mock subprocess to return this for 'rocm-smi' command
        # assert device.memory_used == 512, device.memory_total == 8192
    
    def test_rocm_no_device_returns_empty_list(self):
        """When rocm-smi output contains no device rows, return [] (not error)."""

class TestQueryMPSParsing:
    """_query_MPS correctly parses system_profiler Metal output."""
    
    def test_mps_parses_single_gpu(self):
        """Single Apple GPU → one GPUDevice in result list."""
    
    def test_mps_multiple_gpus_not_supported_yet(self):
        """Multiple GPUs detected → report first device and log a warning.
           (Multi-GPU Apple systems would otherwise misreport.)"""

class TestVRAMEstimation:
    """_estimate_vram_mb handles various model sizes correctly."""
    
    def test_estimate_7b_model(self):
        result = _estimate_vram_mb("llama-3-7b.gguf")  # or pass a model_path fixture
        assert result >= 4000  # typical estimate for 7B models
    
    def test_estimate_3b_model(self):
        """Smaller model → proportionally less VRAM."""
    
    def test_estimate_70b_model(self):
        """Larger model → proportionally more VRAM."""
    
    def test_estimate_unknown_size_defaults_min(self):
        """Unrecognized filename falls back to minimum estimate, not zero."""

class TestNVidasimulateEnvVar:
    """LLAUNCHER_GPU_SIMULATE env var controls simulation mode correctly."""
    
    def test_simulation_disabled_by_default(self):
        """Without the env var set, simulation should be OFF (production path)."""
        if "LLAUNCHER_GPU_SIMULATE" in os.environ:
            del os.environ["LLAUNCHER_GPU_SIMULATE"]
        # Simulate a subprocess call and assert real subprocess.run was called

    def test_simulation_enabled_when_set(self):
        """With the env var set to any value, simulation should be ON."""
        os.environ["LLAUNCHER_GPU_SIMULATE"] = "1"
        # Assert simulated output used instead of subprocess
```

### 4. `tests/unit/test_model_health.py` — ADEQUATE → STRONG

**Add:**

```python
def test_exact_1mb_boundary():
    """File exactly 1,048,576 bytes should be valid (>= threshold) or invalid (> threshold)."""
    # Test the exact boundary to clarify whether it's >= or >
    
def test_zero_byte_file_rejected():
    """Empty file is not 'healthy'."""
    # Already tested — verify this assertion isn't vacuous

def test_permission_denied_file_returns_stat_error():
    """File exists but can't be stat'd → reason should reflect that, not 'too small'."""
```

**Tighten:** Line ~132 `isinstance` + `or hasattr` assertion → replace with single clear check for field presence and non-None value.

### 5. `tests/unit/test_cli.py` — ADEQUATE → STRONG

**Add:**

```python
def test_server_start_port_in_use():
    """Starting server on an occupied port returns error (not silent failure)."""

def test_node_add_invalid_port():
    """Negative port or port > 65535 is rejected by CLI."""

def test_model_list_json_malformed_config():
    """When config file contains malformed JSON, CLI reports error (not crashes with traceback)."""
```

**Fix:** Line ~198 conditional assertion `if call_kwargs: assert …` → remove the condition. Test should always check that the expected call was made when the command runs successfully.

### 6. `tests/unit/test_agent_models_health_api.py` — WEAK → ADEQUATE

**Remove/fix:**
- Line ~199-206: conditional skip on no-GPU CI makes test a no-op → Replace with explicit mocking of GPU collector to simulate "insufficient VRAM" scenario (bypasses need for real hardware)
- Remove unused `_patched_health_client` helper at line 47

**Add:**

```python
def test_vram_error_contains_required_and_available():
    """POST /start-with-eviction with insufficient VRAM returns 409 
    containing both the estimated required and available amounts."""
    # Mock GPUHealthCollector.collect() to return fixed VRAM numbers
    # Assert response body includes both values

def test_estimate_vram_mb_non_7b_sizes():
    """Unit test _estimate_vram_mb directly with known model paths for 3B, 14B, 70B."""
```

### 7. `tests/integration/test_adr_cross_cutting.py` — WEAK → Meaningful Integration

**Remove/fix:**
- Line ~131: `assert "secure-node" in result or exit_code == 0` tautology → remove the OR clause; test should assert on either output content OR exit code, not both with OR
- Line ~162: hardcoded `/home/node/github/...` path → replace with relative import using `pathlib.Path(__file__).parent.parent / "llauncher" / ...`

**Add real integration test:**

```python
class TestFullStackAuthAndHealth:
    """Boot a minimal app with auth middleware + health endpoints, verify they work together."""
    
    def test_auth_gates_health_endpoint_correctly(self):
        """When auth is active, /health is still accessible (exempt). 
        When auth is inactive, /health returns 200. Test both states."""

class TestHealthAndGPUIntegration:
    """Model health endpoint + GPU pre-flight interact correctly in a single request cycle."""
    
    def test_models_health_endpoint_uses_gpu_data(self):
        """GET /models/health should include or reference GPU availability 
        in its response (since model loading depends on it)."""
```

### 8. `tests/unit/test_agent_middleware.py` — STRONG → Minor Fix Only

**Fix:** Dead `self=None` parameter signature at line ~50 (`test_health_exempt_with_empty_key`)  
Change the function from `def test_health_exempt_with_empty_key(self=None): ...` to a module-level function (remove `self`). The body is fine.

### 9. `tests/unit/test_ttl_cache.py` — STRONG → Retain as-Is

No changes needed. This is already well-written and thorough.

---

## Critical Gap Tests (New Files/Methods)

These behaviors are claimed by ADRs but have NO existing tests:

| Behavior | ADR | Test File to Add To |
|----------|-----|---------------------|
| Token size limit / oversized header rejection | 003 | `test_agent_middleware.py` |
| Whitespace-only token handling | 003 | `test_core_settings_auth.py` (listed above) |
| `_estimate_vram_mb` for 3B, 14B, 70B sizes | 006 | `test_gpu_health.py` (new class — listed above) |
| Partial GPU offload scaling (`n_gpu_layers < 999`) in _estimate_vram_mb | 006 | `test_gpu_health.py` (new method — listed above) |
| Exact 1 MB boundary for model health validity | 005 | `test_model_health.py` (listed above) |
| ROCm backend parsing (`_query_ROCM`) regex behavior | 006 | `test_gpu_health.py` (new class — listed above) |
| `ping()` updating `node.status` and `node.last_seen` on success/failure | 003 | `test_remote_node_auth.py` or new file |
| CLI server start port-conflict error path | 004 | `test_cli.py` (listed above) |

---

## Verification Requirements

1. All test files pass after remediation (`pytest tests/`)
2. Test count should increase from ~74 to ~95+ tests (adding gap coverage without inflating count via tautologies)
3. No integration test uses hardcoded absolute paths — all must be environment-agnostic
4. No conditional assertions that can pass vacuously

---

## Acceptance Criteria

- All files rated STRONG remain passing with no regressions
- All files rated ADEQUATE have their known gaps filled
- All files rated WEAK or TAUTOLOGICAL are rewritten to test real behavioral outcomes
- New gap-test additions cover every item in the "Critical Gap Tests" table above
- Test suite is CI-friendly: all tests runnable without GPU hardware (use mocking for hardware-dependent code)
