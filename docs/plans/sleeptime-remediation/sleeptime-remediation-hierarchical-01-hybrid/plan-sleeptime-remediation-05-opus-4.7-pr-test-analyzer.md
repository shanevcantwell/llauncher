# Plan: Sleeptime Remediation #05 — Opus 4.7 PR Test Analyzer

**Status:** Ready for Execution  
**Author:** Strategic Planner (architectural subagent)  
**Target Audience:** Worker agent(s) responsible for test and source remediation  
**Affected ADRs:** 003, 004, 005, 006  
**Scope:** 8 test files + 1 implementation file (`llauncher/core/gpu.py`)

---

## Executive Architecture Summary

The Opus 4.7 PR introduced four new features (auth middleware, CLI subcommands, model health validation, GPU monitoring) with accompanying tests that scored poorly on quality metrics. The review identified **three production code bugs** hidden in the GPU module and **seven weak test files** containing tautological assertions, dead parameters, dead code mocks, and untested critical paths.

This plan executes in four phases:
1. **Fix implementation bugs** in `llauncher/core/gpu.py` (2 confirmed defects + 1 code clarity improvement).
2. **Remediate broken/weak tests** by removing tautological assertions and dead mocks, replacing them with meaningful tests.
3. **Add missing coverage** for behaviors claimed by ADRs but not tested (token normalization edge cases, boundary VRAM sizes, CLI validation paths).
4. **Replace the integration test illusion** in `test_adr_cross_cutting.py` with a real end-to-end scenario that boots FastAPI + middleware + health check together.

---

## Phase 1: Fix Implementation Bugs (`llauncher/core/gpu.py`)

### Bug B: `_query_ROCM()` — Three Sequential Try Blocks (Lines 262–293)

**Current code:**
```python
def _query_ROCM(self) -> dict[str, Any]:
    result: dict[str, Any] = {"devices": []}
    try:
        out = subprocess.run(["rocm-smi", "--showmeminfo=volatile"], ...)
        if out.returncode != 0:
            return result
        # comment-only block — no code before except
    except Exception:
        pass

    # SECOND try block on same variable 'out' (line 280)
    try:
        lines = out.stdout.splitlines() if out.returncode == 0 else []
        for i, line in enumerate(lines):
            match = re.match(r"^\s*GPU[0-9]+\s+.*VRAM\s+Used:\s+(\d+)\s+MiB", line, ...)
            if match:
                idx = int(re.search(r"GPU(\d+)", lines[i]).group(1))
                used = int(match.group(1))
                result["devices"].append(GPUDevice(...))
    except Exception:
        pass

    # NO third try block exists — my earlier read was slightly confused;
    # but the real issue is: if the SECOND try block finds matches, fine.
    # If it catches an exception silently, we fall through and return empty.
    # Additionally, 'out' from line 263 may be undefined if the FIRST try
    # block raised an exception (caught by except, pass) — then lines=281
    # would crash with UnboundLocalError. BUT: that UnboundLocalError is
    # caught by the SECOND try's except → silently returns empty result.

    return result
```

**Root cause:** The first `try` block captures `out`. If it raises, the bare `except Exception: pass` suppresses the error and execution falls into a **second `try` block** that references `out` — creating an `UnboundLocalError`, which is again silently suppressed. Even in the happy path, only one regex pattern (`VRAM Used:`) covers all ROCm output formats; common alternatives like `Volatile GPU memory usage (VBIOS): value: <N>` are not parsed.

**Fix:** Merge into a single try block with two regex patterns and proper flow control.

```python
def _query_ROCM(self) -> dict[str, Any]:
    """Parse ``rocm-smi --showmeminfo=volatile`` output."""
    result: dict[str, Any] = {"devices": []}
    out = subprocess.run(
        ["rocm-smi", "--showmeminfo=volatile"],
        capture_output=True, text=True, timeout=10,
    )
    if out.returncode != 0 or not out.stdout.strip():
        return result

    for line in out.stdout.splitlines():
        # Pattern A: "GPU<N> ... VRAM Used: <N> MiB"
        match = re.match(r"^\s*GPU[0-9]+\s+.*VRAM\s+Used:\s+(\d+)\s+MiB", line, re.IGNORECASE)
        if match:
            idx_match = re.search(r"\bGPU(\d+)\b", line)
            if not idx_match:
                continue
            used = int(match.group(1))
            result["devices"].append(GPUDevice(
                index=int(idx_match.group(1)),
                name=f"ROCm GPU {idx_match.group(1)}",
                used_vram_mb=used,
            ))
            continue

        # Pattern B: "Volatile GPU memory usage (VBIOS): value: <N>"
        vol_match = re.search(
            r"GPU[0-9]+\s+Volatile\s.*?value:\s+(\d+)\s*MiB", line, re.IGNORECASE
        )
        if not vol_match:
            continue
        # Derive GPU index from the first word of the line.
        gpu_num_match = re.match(r"\s*(GPU(\d+))\b", line)
        idx = int(gpu_num_match.group(2)) if gpu_num_match else 0
        result["devices"].append(GPUDevice(
            index=idx,
            name=f"ROCm GPU {idx}",
            used_vram_mb=int(vol_match.group(1)),
        ))

    return result
```

### Bug C: `_query_MPS()` — Dead Regex Matches (Lines 295–318)

**Current code:**
```python
for line in out.stdout.splitlines():
    match = re.search(r"(\w[\w\s.]+)\s*\n.*?Chipset Model", line)        # ← dead: never used
    name_match = re.match(r".*\n(.+)\s+Chipset Model", out.stdout, ...)  # ← dead: never used
# Result: always appends a single device with estimated memory regardless of actual output.
result["devices"].append(GPUDevice(index=0, name="Apple Silicon (MPS)", total_vram_mb=_estimate_apple_unified_mem()))
```

**Fix:** Either properly use the regex matches to extract GPU names, or remove dead code and produce a single deterministic result:

```python
def _query_MPS(self) -> dict[str, Any]:
    """Query Apple MPS via system_profiler SPDisplaysDataType."""
    result: dict[str, Any] = {"devices": []}
    if not is_apple_mps_available():
        return result
    try:
        out = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return result

        total_mem_mb = _estimate_apple_unified_mem()

        # Attempt to extract GPU names from system_profiler output.
        gpu_names: list[str] = []
        for line in out.stdout.splitlines():
            name_match = re.match(r"^\s*(\w[\w\s.\-]+)\s*GPU", line, re.IGNORECASE)
            if name_match:
                gpu_name = name_match.group(1).strip()
                if gpu_name and gpu_name not in ("Apple", "Metal") and gpu_name not in gpu_names:
                    gpu_names.append(gpu_name)

        # If we found named GPUs, create one device per GPU.
        if gpu_names:
            for idx, gname in enumerate(gpu_names):
                result["devices"].append(GPUDevice(
                    index=idx, name=gname, total_vram_mb=total_mem_mb // len(gpu_names),
                ))
        else:
            # Fallback: single Apple Silicon device.
            result["devices"].append(GPUDevice(
                index=0, name="Apple Silicon (MPS)", total_vram_mb=total_mem_mb,
            ))

    except Exception:
        pass

    return result
```

### Code Clarity: `LLAUNCHER_GPU_SIMULATE` Expression (Line ~140)

**Current:**
```python
data = self._query_NVIDIA(simulated_output=not os.environ.get("LLAUNCHER_GPU_SIMULATE", "") == "")
```

**Issue:** While functionally correct (`simulated_output=False` in production), the expression relies on Python operator precedence (`==` before `not`) which makes it hard to read at a glance. The test file calls this pattern "Bug A" and suggests the ambiguity itself is worth fixing.

**Fix:** Replace with an explicit variable:
```python
simulate_env = os.environ.get("LLAUNCHER_GPU_SIMULATE", "")
simulated_output = bool(simulate_env)  # True if env var is set to any non-empty value
data = self._query_NVIDIA(simulated_output=simulated_output)
```

Or, more explicitly with docstring:
```python
# simulated_output=True triggers test-mode parsing from the string env-var.
# simulated_output=False runs real nvidia-smi CLI.
_simulate_key = os.environ.get("LLAUNCHER_GPU_SIMULATE")
simulated_output = _simulate_key is not None and _simulate_key != ""
data = self._query_NVIDIA(simulated_output=simulated_output)
```

---

## Phase 2: Remediate Broken/Weak Tests

### File 1: `tests/unit/test_core_settings_auth.py` (~45 lines) — WEAK

**Problem:** Three tests all do the same thing (importlib.reload + assert value). Fragile module-reloading pattern, no edge-case coverage.

**Strategy:** Replace the entire file with a fixture-based approach that creates fresh settings instances. Remove `importlib.reload`; instead test via environment-variable fixtures and re-import in a controlled namespace.

#### Specific Test Cases:

| # | Test Name | Description | Severity |
|---|-----------|-------------|----------|
| 1 | `test_default_api_key_is_none` | Keep — but use conftest fixture to isolate env | Reduce fragility |
| 2 | `test_api_key_from_env` | Keep — same improvement as above | Reduce fragility |
| **NEW** | `test_whitespace_token_stripped_to_empty_then_none` | Set `LAUNCHER_AGENT_TOKEN="   \t  "`, assert `AGENT_API_KEY is None` or equals `"    \t  "` depending on whether settings strips whitespace. (Check if settings does `.strip()` — it currently doesn't, so this tests current behavior and flags a missing strip.) | **High** |
| **NEW** | `test_oversized_token_accepted_as_is` | Set token to 512-char string; assert AGENT_API_KEY is set. Tests that there's no hard cap enforced at settings level (and documents that this decision exists). | Medium |
| **NEW** | `test_api_key_with_special_chars` | Set token to `"s3cr3t!@#$%^&*()"`; assert it mirrors the env value exactly. | Low |

#### Conftest Fixture (add to `tests/conftest.py`):

```python
@pytest.fixture()
def fresh_settings():
    """Return a clean import of settings module with controlled environment."""
    # Clear relevant env vars first, then let the test override via patch.dict in context.
    yield lambda: __import__('llauncher.core.settings')  # placeholder — see note below
```

**Note for Worker:** The simplest approach without rewriting all imports is to create a small helper:
```python
def _read_api_key_from_env(env_token):
    """Read AGENT_API_KEY value after setting env var."""
    old = os.environ.pop("LAUNCHER_AGENT_TOKEN", None)
    try:
        if env_token is not None:
            os.environ["LAUNCHER_AGENT_TOKEN"] = env_token
        # Re-import to pick up fresh value — or better, re-read the line from settings.py directly.
        key_value = os.getenv("LAUNCHER_AGENT_TOKEN")
        if key_value == "":
            key_value = None
        return key_value
    finally:
        if old is None:
            os.environ.pop("LAUNCHER_AGENT_TOKEN", None)
        else:
            os.environ["LAUNCHER_AGENT_TOKEN"] = old
```

**Action:** Replace the file entirely with the new test structure using `_read_api_key_from_env`. Delete `importlib.reload(settings)` patterns.

### File 2: `tests/unit/test_remote_node_auth.py` (~60 lines) — WEAK/TAUTOLOGICAL

**Problem:** First test mocks `httpx.Client.__enter__` to return None (line ~13), then asserts on `_get_headers()` which is just a 3-line dict builder. No wire-level verification that `ping()`, `get_status()`, `start_server()` actually pass `X-Api-Key` to HTTP calls.

**Strategy:** Replace the first test with a real `TestClient`-based integration where the RemoteNode makes an actual (mocked) HTTP call, and assert on the intercepted request headers. Remove dead mock setup (`patch.object(httpx.Client, "__enter__")`).

#### Specific Test Cases:

| # | Current / Proposed | Action |
|---|---------------------|--------|
| 1 | `test_node_with_api_key_includes_header` | **Rewrite** — use `httpx.mock.MockTransport` to intercept the actual HTTP request from `node.ping()`, then assert `request.headers["X-Api-Key"] == "mykey"` on the captured Request object. Remove dead `__enter__/mock_instance` setup. |
| 2 | `test_node_without_api_key_no_extra_headers` | Keep — it's already a simple, correct test of `_get_headers()`. |
| 3 | `test_node_empty_api_key_treated_as_none` | Keep — tests the constructor normalization. Add an assertion that `_get_headers()` returns `{}` after normalization (it does but not asserted). |

#### Rewrite for Test #1 (wire-level verification):

```python
def test_node_with_api_key_includes_header():
    """A node with api_key set passes X-Api-Key in actual HTTP requests."""
    import httpx
    
    captured_requests = []
    
    def transport(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"status": "ok"})
    
    node = RemoteNode("test", "localhost", port=8765, api_key="mykey")
    
    # Monkey-patch _get_client to use mock transport.
    original_get_client = node._get_client
    try:
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            node._get_client = lambda: client  # type: ignore[assignment]
            node.ping()
        
        assert len(captured_requests) == 1, "Expected exactly one HTTP request from ping()"
        assert captured_requests[0].headers.get("X-Api-Key") == "mykey", \
            "X-Api-Key header must be present with correct value"
    finally:
        node._get_client = original_get_client
```

**Action:** Rewrite `test_remote_node_auth.py` replacing the first test with the wire-level approach. Remove dead code from the mock setup block (lines 12–27).

### File 3: `tests/unit/test_gpu_health.py` (~160 lines) — WEAK

#### Tautology at Line ~84 (`assert isinstance(result, object)`)

```python
def test_lifecycle_processes_mapped(self):
    ...
    result = collector.refresh()
    assert isinstance(result, object)  # ← ALWAYS TRUE. Fix:
    # → assert isinstance(result, GPUHealthResult)
```

#### VRAM Consistency Only Checks Keys, Not Values (Line ~97–103)

**Current:**
```python
def test_vram_before_and_after_start(self):
    before = collector.get_health(force_refresh=True)
    after = collector.get_health(force_refresh=True)
    assert set(before.keys()) == set(after.keys())  # ← only checks keys, not VRAM values
```

**Fix:** Also verify `before["backends"] == after["backends"]` and that device dicts have matching sub-keys. Since no GPU is present in CI, the expected result is empty lists — make this explicit:

```python
assert before == after, "Empty-health results must be idempotent across calls"
assert before["backends"] == []  # explicit, not implicit via key-only check
```

#### Missing Assertions Table

| # | Test Name | Description | Severity |
|---|-----------|-------------|----------|
| **NEW** | `test_rocm_query_with_simulated_output` | Mock subprocess for `rocm-smi`, provide output string matching both regex patterns (Pattern A and B). Assert that devices list contains entries with correct index/used_vram. Tests the fixed `_query_ROCM`. | **High — Bug B fix verification** |
| **NEW** | `test_mps_query_with_simulated_output` | Mock subprocess for `system_profiler`, provide output containing GPU names. Assert devices list contains named Apple GPUs (not just generic fallback). | **High — Bug C fix verification** |
| **NEW** | `test_is_available_returns_bool_for_all_types` | Call `is_available("nvidia")`, `is_available("rocm")`, `is_available("mps")`. Assert each returns bool. Currently only tests "nvidia". | Low–Medium |
| **NEW** | `test_nvidia_simulation_with_env_var_set` | Set `LLAUNCHER_GPU_SIMULATE="fake output"` env var, call `_query_NVIDIA()` with simulated_output=True (direct call, bypassing _try_NVIDIA since that checks shutil_which). Assert parsed device matches the fake output. Verifies Bug A clarification works. | Medium |
| **EXISTING** | `test_is_available_returns_bool` | Keep but add more gpu_type variants. | Reduce |

#### Rewrite for Test `TestLifecycleProcessesMapped`:

```python
class TestLifecycleProcessesMapped:
    def test_lifecycle_processes_mapped(self):
        from llauncher.core.gpu import GPUHealthCollector, GPUHealthResult
        
        collector = GPUHealthCollector()
        result = collector.refresh()
        
        assert isinstance(result, GPUHealthResult), \
            "refresh() must return a GPUHealthResult instance"
        assert hasattr(result, "devices")
        assert hasattr(result, "backends")
        # In CI without GPUs: empty devices list is expected.
        assert result.backends == []  # idempotent across calls
```

### File 4: `tests/unit/test_model_health.py` (~140 lines) — ADEQUATE (minor fixes)

| # | Issue | Fix |
|---|-------|-----|
| **Missing** | No test for exactly **1,048,576 bytes** (exactly 1 MiB = boundary). Current test uses `+1` byte past the threshold. | Add: write file with exactly `b"x" * 1048576`, assert `result.valid is True`. |
| **Weak** | `test_last_modified_populated_for_valid`: assertion uses `or hasattr(result, "last_modified")` — always true. | Fix to: `assert isinstance(result.last_modified, datetime)` (and verify it's not None). |

### File 5: `tests/unit/test_cli.py` (~290 lines) — ADEQUATE

| # | Issue | Location | Fix |
|---|-------|----------|-----|
| **Dead assert** | Line ~198: `if call_kwargs: assert ...` passes silently when mock not called. | `test_start_with_explicit_port` | Always assert on the call_args (it will be set because we returned a fixed tuple from start_server). |
| **Missing** | No port-conflict error path tested. CLI doesn't validate ports; just calls `state.start_server(name, port=port)`. Add test with mock that returns `(False, "Port 8081 already in use", None)` → assert exit_code == 1 and message in output. | After existing server tests | New test: `test_start_port_conflict_error` |
| **Missing** | No invalid port validation (>65535 or negative). Typer accepts any int; no pre-validation. Add test that demonstrates current behavior (accepts >65535, returns error from server) to document this gap. | New section under "Negative / edge cases" | `test_start_negative_port_rejected`, `test_start_over_65535_rejected` — assert they produce an error or are accepted silently (documenting current behavior). |
| **Missing** | No malformed config file JSON tested. ConfigStore reads from JSON; if corrupt, should fail gracefully. Add test: write invalid JSON to config path, run `config validate`, expect non-zero exit. | Under "Negative / edge cases" | `test_config_validate_malformed_json` |

### File 6: `tests/unit/test_agent_models_health_api.py` (~206 lines) — WEAK

| # | Issue | Fix |
|---|-------|-----|
| **No-op test** | `test_vram_error_contains_required_and_available`: VRAM pre-flight gated by actual GPU presence. In CI, no GPU → `_check_vram_sufficient` returns `(True, None)` → request succeeds (200) or errors for unrelated reasons. The `if response.status_code == 409:` is always skipped in CI. | **Rewrite:** Patch `_estimate_vram_mb` and/or mock the VRAM check to force a 409 response. Use monkeypatch: `@monkeypatch.setattr("llauncher.agent.routing._check_vram_sufficient", lambda x: (False, {"error": "insufficient_vram", "required_mb": 7168, "available_mb": 2048}))`. |
| **Unused helper** | `_patched_health_client()` creates a client but is never used — all tests build their own. | Delete the function; consolidate to one shared pattern in conftest. |

### File 7: `tests/integration/test_adr_cross_cutting.py` (~170 lines) — WEAK

**Major Issues:**
- Line ~89 tautology: `assert "secure-node" in result.output or result.exit_code == 0` (either condition likely true regardless of behavior).
- Line ~162 hardcoded absolute path `/home/node/github/llauncher/...` — source inspection, not logic testing.
- None of the 9 "integration" tests actually boot the full stack together.

**Strategy:** Phase 4 covers this fully below. For now in Phase 2: delete or mark as deprecated those tests that are tautological. Focus on keeping only the genuinely useful ones (TTL cache tests, GPU no-backend test).

---

## Phase 3: Add Missing Coverage

This phase adds concrete test cases for behaviors claimed by ADRs but not yet tested. Organize into new test functions in existing files.

### Auth Edge Cases (`tests/unit/test_agent_middleware.py`)

| # | Test | Description | File / Lines |
|---|------|-------------|--------------|
| 1 | Fix `self=None` parameter | Line 87: Change `def test_health_exempt_with_empty_key(self=None):` → remove `self` entirely. It's a module-level function, not a class method. | Line 87 |
| 2 | **NEW** `test_whitespace_api_key_rejected_as_403` | Send header with only whitespace (`"   "`). Should return 403 (not 401) because the header is present but doesn't match. Verifies the middleware's `"header present → 403"` logic with non-empty strings. | New test function |
| 3 | **NEW** `test_token_normalized_on_comparison` | If settings strips whitespace from token, create app with stripped expected_token and send whitespace-variant header; verify correct auth flow. (Or: if it doesn't strip, document current behavior.) | New test function |

### CLI Edge Cases (`tests/unit/test_cli.py`)

| # | Test | Description |
|---|------|-------------|
| 1 | **NEW** `test_start_port_conflict_error` | Mock `state.start_server` to return `(False, "Port 8081 already in use", None)`. Invoke CLI with `server start mymodel --port 8081`. Assert exit_code == 1 and error message in output. |
| 2 | **NEW** `test_start_over_max_port_accepted_by_typer_but_rejected` | Typer doesn't validate port range; invoke with `--port 99999`. Document current behavior — likely accepted by Typer, rejected by start_server or OS. |
| 3 | **NEW** `test_malformed_config_json_rejected` | Write invalid JSON to CONFIG_PATH (`{"broken"`). Invoke `config validate some-model`. Assert exit_code != 0 and error mentions JSON/parsing. |

### VRAM Heuristic Coverage (`tests/unit/test_agent_models_health_api.py`)

| # | Test | Description |
|---|------|-------------|
| 1 | **NEW** `test_vram_heuristic_3b_model` | Import `_estimate_vram_mb`, call with model_path `"llama-3-3b.gguf"`. Assert result ≈ 3072 MB (3 × 1024). |
| 2 | **NEW** `test_vram_heuristic_14b_model` | Call with `"mistral-14b.Q4_K_M.gguf"`. Assert ≈ 14336 MB. |
| 3 | **NEW** `test_vram_heuristic_70b_default_fallback` | Call with path that has no recognizable param pattern (e.g., `"mymodel.bin"`). Assert default 7B estimate = 7168 MB. |
| 4 | **NEW** `test_exact_1mb_boundary_file_is_valid` | Write file of exactly `1048576` bytes to temp file, call `check_model_health`. Assert `valid=True` (boundary is inclusive at `_MIN_SIZE_BYTES`). |

### Remote Node Wire-Level Tests (`tests/unit/test_remote_node_auth.py`)

Already covered in Phase 2 above (rewritten test #1).

---

## Phase 4: Integration Test Realism

### Current State of `tests/integration/test_adr_cross_cutting.py`

The file has 9 tests but **none** actually boot a full-stack FastAPI app with auth middleware, model health endpoints, and GPU pre-flight working together. Most test isolated components in fake configurations. One test reads source code (line ~162) rather than testing logic.

### Replacement Strategy

Remove the existing file contents (or rename to `.bak`) and replace with a minimal but real integration test:

```python
"""Integration tests: FastAPI + middleware + model health working together end-to-end."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


class TestFullStackAuthAndHealth:
    """End-to-end: boot a minimal app with auth, verify auth gate blocks then lets through.

    This tests ADR-003 (auth middleware) + ADR-005 (model health endpoint)
    interacting as they would in production — not just their isolated behaviors.
    """

    @pytest.fixture
    def full_stack_app(self, tmp_path):
        """Create a FastAPI app with auth middleware + model health endpoints."""
        from fastapi import FastAPI
        from llauncher.agent.middleware import AuthenticationMiddleware
        
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware, expected_token="e2e-test-token")

        @app.get("/health")
        def health():
            return {"status": "ok"}

        @app.post("/start-with-eviction/{model_name}")
        def start_endpoint(model_name: str):
            from llauncher.core.model_health import check_model_health
            mh = check_model_health(f"/nonexistent/models/{model_name}.gguf")
            if not mh.valid:
                raise Exception("Model health check failed")
            return {"started": model_name, "port": 8081}

        @app.get("/models/health")
        def models_health():
            return []

        @app.get("/models/health/{model_name}")
        def model_health_detail(model_name: str):
            from llauncher.core.model_health import check_model_health
            return check_model_health(f"/nonexistent/models/{model_name}.gguf").model_dump()

        yield app

    def test_e2e_unauthenticated_start_rejected(self, full_stack_app):
        """Auth gate blocks POST /start-with-eviction without token."""
        client = TestClient(full_stack_app)
        resp = client.post("/start-with-eviction/mistral-7b")
        assert resp.status_code == 401
        data = resp.json()
        assert "Authentication" in data["detail"]

    def test_e2e_authenticated_start_blocked_by_health(self, full_stack_app):
        """Valid auth + nonexistent model → health check fails (not VRAM or server crash)."""
        client = TestClient(full_stack_app)
        resp = client.post(
            "/start-with-eviction/mistral-7b",
            headers={"X-Api-Key": "e2e-test-token"},
        )
        assert resp.status_code in (500, 400, 422), \
            "Should fail due to health check or model not found"

    def test_e2e_health_exempt_from_auth(self, full_stack_app):
        """/health endpoint accessible without token even when auth is active."""
        client = TestClient(full_stack_app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_e2e_model_health_detail_requires_auth(self, full_stack_app):
        """/models/health/{name} is protected by auth middleware."""
        client = TestClient(full_stack_app)
        resp_no_key = client.get("/models/health/mistral")
        assert resp_no_key.status_code == 401
        
        resp_with_key = client.get(
            "/models/health/mistral",
            headers={"X-Api-Key": "e2e-test-token"},
        )
        # With the patched nonexistent path, should return valid=false with exists=false.
        assert resp_with_key.status_code == 200
        data = resp_with_key.json()
        assert data["exists"] is False


class TestTTLCacheCrossModule:
    """TTL cache used by both model_health and gpu modules behaves consistently."""

    def test_cache_isolation_between_modules(self):
        """A key in model_health's cache doesn't interfere with GPU cache."""
        from llauncher.util.cache import _TTLCache
        
        model_cache = _TTLCache(ttl_seconds=60)  # as used by model_health
        gpu_cache = _TTLCache(ttl_seconds=5)     # as used by gpu collector
        
        model_cache.set("key_x", {"module": "model"})
        gpu_cache.set("key_y", {"module": "gpu"})
        
        assert model_cache.get("key_x") == {"module": "model"}
        assert model_cache.get("key_y") is None   # not visible to other cache
        assert gpu_cache.get("key_y") == {"module": "gpu"}
        assert gpu_cache.get("key_x") is None     # not visible to other cache
```

### What Gets Deleted from Original File

| Test | Reason | Action |
|------|--------|--------|
| `test_cli_node_add_with_api_key` (Tautology at line ~89) | Either `"secure-node" in output` or `exit_code == 0` will likely be true regardless of behavior. Source-inspection of registry file is fragile and machine-dependent. | Delete — the CLI already has this tested well in `test_cli.py`. |
| `test_vram_heuristic_estimates_for_7b_model` (Hardcoded path at line ~162) | Reads source code to assert function name exists, instead of exercising actual logic. Breaks on other machines and does not test behavior. | Delete — VRAM heuristic tested in Phase 3 via `_estimate_vram_mb` unit tests. |
| `TestCliAndAuthIntegration::test_cli_node_add_with_api_key` | Redundant with CLI tests + fragile node registry file reading. | Delete. |

---

## Phased Implementation Roadmap

### Sequence for Worker Agents

Execute phases sequentially — each phase has zero dependencies on subsequent phases (except Bug B/C fixes must be in place before ROCm/MPS integration tests pass).

```
Phase 1: Fix gpu.py implementation bugs ────────────────────── BLOCKING
  ├─ 1.1 Fix _query_ROCM() (merge try blocks, add Pattern B)    [~20 min]
  ├─ 1.2 Fix _query_MPS() (remove dead regex, use real names)   [~15 min]  
  └─ 1.3 Clarify LLAUNCHER_GPU_SIMULATE expression               [~5 min]

Phase 2: Remediate broken/weak test files ───────────────────
  ├─ 2.1 Fix test_agent_middleware.py (remove self=None param)   [~10 min]
  ├─ 2.2 Rewrite test_core_settings_auth.py                     [~30 min]
  ├─ 2.3 Rewrite test_remote_node_auth.py (wire-level test)     [~25 min]
  ├─ 2.4 Fix tautologies in test_gpu_health.py                  [~15 min]
  ├─ 2.5 Fix weak assert in test_model_health.py                [~10 min]
  ├─ 2.6 Fix dead-assert + add CLI edge cases                   [~30 min]
  └─ 2.7 Rewrite VRAM pre-flight test in test_agent_models      [~20 min]

Phase 3: Add missing coverage tests ─────────────────────────
  ├─ 3.1 Whitespace API key + oversize token (settings)         [~15 min]
  ├─ 3.2 Whitespace auth bypass (middleware)                    [~10 min]
  ├─ 3.3 Port conflict / malformed JSON (CLI)                   [~20 min]
  ├─ 3.4 VRAM heuristic 3B/14B/70B unit tests                  [~15 min]
  ├─ 3.5 Exact 1MB boundary test                                [~10 min]
  └─ 3.6 ROCm/MPS simulated output tests                        [~25 min]

Phase 4: Replace integration test with real end-to-end ──────
  ├─ 4.1 Delete tautological cross-cutting tests                [~5 min]
  └─ 4.2 Write TestFullStackAuthAndHealth class                 [~20 min]
```

**Estimated total: ~3–4 hours of focused worker execution.**

### Dependency Map

```
Phase 1 (gpu.py fixes) ← required for Phase 3 ROCm/MPS tests to pass
      │
      ├──→ Phase 2 (test remediation, independent per-file)
      │       ├── test_agent_middleware.py ── no deps
      │       ├── test_core_settings_auth.py ── no deps
      │       ├── test_remote_node_auth.py ── no deps
      │       ├── test_gpu_health.py ──────── depends on Phase 1 fixes for new ROCm/MPS tests
      │       ├── test_model_health.py ────── no deps
      │       ├── test_cli.py ─────────────── no deps
      │       └── test_agent_models_health_api.py ── no deps (but needs monkeypatch fix)
      │
      └──→ Phase 3 (new tests, mostly independent)
              │
              └──→ Phase 4 (integration tests, uses nothing from prior phases)
```

### Parallelization Strategy

Phases can execute in the following parallel groups:
- **Group A:** Phase 1 alone (single file, sequential within it).
- **Group B:** All Phase 2 remediations — each test file is independent; spawn a worker per file.
- **Phase 3** runs after Phase 2 completes (some tests overlap with remediated files).
- **Phase 4** can run independently of all prior phases.

### Entry/Exit Criteria Per Phase

| Phase | Entry | Exit |
|-------|-------|------|
| 1 | None | `llauncher/core/gpu.py` has no dead try blocks, `_query_ROCM` and `_query_MPS` use regex results meaningfully, env-var expression is explicit. All existing GPU tests still pass. |
| 2 | Phase 1 complete (for ROCm/MPS tests) | No tautological assertions (`isinstance(..., object)` removed), no dead mock setup, no `self=None` parameters, no unused helper functions. Each test file ≤ original line count + additions from Phase 3. |
| 3 | Phase 2 complete | New tests cover: whitespace tokens, oversized keys, port conflict CLI, malformed JSON config, VRAM 3B/14B/70B heuristics, exact 1MB boundary, ROCm Pattern B output parsing, MPS named GPU extraction. |
| 4 | Phases 2+3 complete | Integration test file contains ≥4 real end-to-end scenarios with zero hardcoded absolute paths, zero source-code-reading assertions, zero tautological `or` conditions. |

---

## Risk & Observability Strategy

### Known Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `_query_ROCM` regex patterns don't match real ROCm output on production systems | Medium | Low — function returns empty list, falls through to next backend or reports no GPU. No crash. | Add integration test with mock ROCm output covering both Pattern A and B. |
| `importlib.reload(settings)` approach removed but some test setup relied on module-level state leakage | Low | Medium — if other tests in the suite reload settings, they may fail temporarily during parallel execution. | Ensure `_reset_cache` fixture (in conftest) doesn't touch settings; add a separate `_clear_settings_env` fixture if needed. |
| Mock transport monkey-patching in RemoteNode test may conflict with httpx global state | Low | Low — `with` block scopes the mock client to single method call. Original code was already fragile (mocking `__enter__` return None). | Use local `_get_client` replacement scoped within try/finally. |
| Integration test uses `/nonexistent/models/{name}.gguf` paths which bypass real health checks in CI | Medium | Low — this is by design to test auth + model-health interaction without needing a filesystem with 1MB+ files. The integration still tests the actual middleware routing chain end-to-end. | Keep as-is; it's the correct approach for an auth-gateway E2E test. |
| Removing `_patched_health_client()` unused helper may break any external consumer importing it (unlikely — private function) | Negligible | Low | Function is a module-private helper with underscore prefix in tests/ only. Safe to delete. |

### Observability: How We Know the Remediation Worked

1. **All `pytest` passes** — run full test suite after each phase; no regressions in existing GREEN tests.
2. **New coverage report** — run `pytest --cov=llauncher` and verify:
   - `gpu.py`: `_query_ROCM` branch coverage ≥ 80% (both Pattern A + B).
   - `gpu.py`: `_query_MPS` covers both named-GPU and fallback paths.
   - `middleware.py`: all four dispatch branches covered (no token, wrong token, empty header, exempt path).
3. **Line-by-line audit** for tautology removal:
   ```bash
   grep -rn 'isinstance.*object' tests/    # should return 0 matches after Phase 2
   grep -rn 'self=None' tests/             # should return 0 matches
   grep -rn '\bor\b.*hasattr\|assert.*or exit_code\|assert.*in output or' tests/integration/  # should return 0 tautological asserts
   ```

### Rollback Plan

If a Phase 1 fix to `gpu.py` causes downstream test failures:
- The three try-block patterns are **additive** (Pattern A + B), not replacing a single working parser. If neither matches real hardware, the behavior is identical to before (empty result).
- Reverting means restoring the original `_query_ROCM` and `_query_MPS`; this is safe because nothing in production depends on ROCm/MPS working if those tools aren't available anyway.

---

## Appendix: File-by-File Action Summary

| File | Current State | Phase | Actions |
|------|--------------|-------|---------|
| `llauncher/core/gpu.py` | BUG B, C + clarity issue A | 1 | Rewrite `_query_ROCM()`, rewrite `_query_MPS()`, clarify env-var expression |
| `tests/unit/test_agent_middleware.py` | STRONG (line 87: self=None) | 2+3 | Fix line 87; add whitespace-token test |
| `tests/unit/test_core_settings_auth.py` | WEAK | 2+3 | Rewrite with env helper, add whitespace/oversize/special-chars tests |
| `tests/unit/test_remote_node_auth.py` | WEAK (dead mocks) | 2+3 | Wire-level monkey-patch test for ping(); keep simple _get_headers tests |
| `tests/unit/test_gpu_health.py` | WEAK (tautologies, gaps) | 2+3 | Fix isinstance(object), VRAM consistency, add ROCm/MPS simulated tests |
| `tests/unit/test_model_health.py` | ADEQUATE (minor) | 2+3 | Add exact 1MB boundary test; fix last_modified weak assert |
| `tests/unit/test_cli.py` | ADEQUATE (gaps) | 2+3 | Fix dead-assert, add port-conflict/malformed JSON/invalid-port tests |
| `tests/unit/test_agent_models_health_api.py` | WEAK (no-op test, unused helper) | 2+3 | Monkeypatch VRAM check to force 409; delete `_patched_health_client()` |
| `tests/integration/test_adr_cross_cutting.py` | WEAK (tautologies, source-inspection, hardcoded paths) | 4 | Replace with real full-stack auth + health integration tests |
| `tests/unit/test_ttl_cache.py` | STRONG | — | **No changes** |
