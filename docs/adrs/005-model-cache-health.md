# ADR-005: Model Cache Health Validation in Start/Stop Flow

**Status:** Draft  
**Date:** 2026-04-26  

## Context

When starting a model server via `llauncher`, the system validates that a `model_name` exists in configuration but **never checks whether the actual model files exist on disk**. This means operators can invoke "start" for models whose weights are missing, corrupted, or incomplete — wasting time and producing misleading errors.

Session findings identified this as Finding W8: "Missing pre-flight validation of model file existence." The review showed that `state.start_with_eviction()` accepts any configured model name and only fails at the llama-server process level when it can't load weights (if at all).

Additionally, sessions noted operators are blind to GPU memory constraints — starting a 13B model on an already-full GPU with no indication.

### Current Flow
```
state.start_with_eviction(model_name="mistral-7b", port=8081)
    ↓
check if "mistral-7b" in configs  ← ONLY validation
    ↓  
start llama-server process        ← model_path not checked
    ↓
poll /status                      ← may fail hours later with OOM or missing weights
```

## Decision

### Option Chosen: Pre-flight Model Health Validation + Cache Dashboard Endpoint

Add a two-layer check before starting any server, plus a persistent cache manifest that tracks download/verify status.

**Layer 1 — Pre-flight validation in `state.start_with_eviction()`:**
```python
def start_server(self, model_name, port, ...):
    config = self.config_store.get_model(model_name)
    
    # NEW: Validate model file exists before attempting to start
    health = check_model_health(config.model_path)
    if not health.valid:
        return False, f"Model cache invalid: {health.reason}"
    
    # Continue with existing flow...
```

**Layer 2 — Model Health API Endpoint:**
```
GET /models/health         → list all models + file status for each
GET /models/health/<name>  → detailed health check for one model
```

Response shape:
```json
{
    "model_name": "mistral-7b-instruct",
    "config_path": "~/.llauncher/models.json",
    "file_status": {
        "exists": true,
        "readable": true,
        "size_bytes": 13421772800,
        "last_modified": "2026-04-25T18:30:00Z",
        "safe_to_load": true  // file > 1MB (heuristic for corruption check)
    },
    "estimated_vram_mb": null  // populated only if GPU metrics available (see ADR-006)
}
```

**Optional enhancement — `models.json` manifest tracking:**
When models are downloaded via external tools, operators can register them:
```bash
llauncher model register mistral-7b --path /data/models/mistral-7b-instruct-Q4_K_M.gguf
```
This writes to the config and optionally sets a "verified" flag after a health check passes.

### Testing Requirements
- Unit test: `check_model_health()` returns valid for existing readable file, invalid for missing/corrupted
- Integration: start server with nonexistent model_path → rejected before process spawn (not OOM later)
- API: `/models/health` returns correct status for configured models; handles missing files gracefully
- Edge case: model path is a symlink → resolve and validate target exists

### UI Impact (Streamlit Tab — "Model Registry")
A new tab in the Streamlit UI showing:
- Table of all configured models with file existence ✓/✗ indicators
- Model name, path, size, last modified date
- Download/update link placeholder for future integration
- Status column: "ready", "missing", "corrupted", "unknown"

## Consequences

**Positive:**
- Operators get immediate feedback when model files are missing or corrupted
- Prevents wasted GPU time starting servers that will inevitably fail
- Foundation for future download management and version tracking
- Makes the system more reliable in ephemeral environments (containers, shared storage)

**Negative:**
- Adds I/O overhead on every start attempt — should be cached and only re-checked periodically
- Requires decision on validation depth: simple existence check vs. full GGUF header verification
- Model path could be network-mounted; performance implications for large file trees

**Open Questions:**
1. How deep is the validity check? Just `os.path.exists()` + size, or also parse GGUF magic bytes and validate header? (Recommendation: start with existence+size, add header validation as Phase 2)
2. Should failed starts be retried automatically after file restoration? (Defer to review cycle decision)
