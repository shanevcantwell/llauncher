# M6 Design — Multi-Backend Adapter Layer (vLLM)

**Status:** Draft
**Date:** 2026-05-05
**Predecessor:** [m5-design.md](m5-design.md)
**Successor:** M7 release

## Goal

Generalize llauncher beyond `llama-server` so it can also manage `vllm` (and, in principle, any inference engine that exposes a similar HTTP-shaped serving contract). The architecture work was scaffolded in M1 (the `BackendKind` enum on `ModelConfig`); M6 lands the adapter layer, the vLLM implementation, and the ADR amendments that fold backend-awareness into existing decisions.

The work is tracked under [Issue #42](https://github.com/shanevcantwell/llauncher/issues/42), which already contains a detailed sketch. This design doc translates that sketch into milestone slices.

## What Already Exists

- `BackendKind` enum on `ModelConfig` (M1) — currently only `LLAMA_SERVER` defined; the discriminator is in place.
- `ModelConfig` is a flat dataclass; M6 turns it into a discriminated union.
- `core/process.py:build_command` is hard-coded for llama-server. M6 turns this into a dispatcher.
- `core/model_health.py` has GGUF-shaped checks. M6 generalizes via per-adapter `validate_model_path`.
- `core/gpu.py:estimate_vram_mb` has llama-server heuristics. M6 generalizes via per-adapter `estimate_vram_mb`.
- The argv sentinel (`--alias <model>`) is llama-server-specific. M6 picks env-var or per-backend sentinel per ADR-008's amendment notes.

## Architectural Frame

The backend adapter is an instance of the **adapter pattern** with a discriminated union. Reference implementation: [`prompt-prix`](https://github.com/shanevcantwell/prompt-prix) provides the structural pattern.

```python
# llauncher/backends/__init__.py  (new)
class BackendAdapter(Protocol):
    """Per-backend operations; instances are stateless."""
    kind: BackendKind

    def build_command(self, config: ModelConfig, port: int) -> list[str]: ...
    def validate_model_path(self, path: str) -> ModelHealth: ...
    def estimate_vram_mb(self, config: ModelConfig) -> int: ...
    def readiness_endpoint(self, port: int) -> str: ...
    def sentinel_kwargs(self, model_name: str) -> dict[str, str]: ...
```

Each concrete adapter (`LlamaServerAdapter`, `VLLMAdapter`) implements this protocol. The tool layer dispatches purely on `config.kind`:

```python
# llauncher/operations/start.py
adapter = backends.for_kind(config.kind)
cmd = adapter.build_command(config, port)
```

**No conditionals scattered through state/process code.** All backend-aware logic is funneled through the adapter.

## Work Breakdown

### Slice 17 — ADR-012 (the new ADR)

Per Issue #42's "Future ADR-012" outline:

- Pattern selection (adapter + discriminated union vs alternatives).
- `BackendKind` values enumerated; designed-to-extend.
- Migration: existing configs default to `"llama_server"` (already true since M1).
- Per-backend responsibilities (the `BackendAdapter` protocol surface).
- How backend awareness interacts with the v2 architecture (ops, sentinel, lockfile).
- Open: is `kind` part of model identity? **Pin: yes** — same weights under two backends are two configs.

### Slice 18 — Adapter scaffolding + extract LlamaServerAdapter

- Create `llauncher/backends/{__init__.py, llama_server.py}`.
- `BackendAdapter` Protocol + `for_kind(kind)` registry function.
- Move the existing `core/process.py:build_command` body into `LlamaServerAdapter.build_command`. The old function becomes a one-line dispatch.
- Same for `core/model_health.py` (GGUF checks → `LlamaServerAdapter.validate_model_path`) and `core/gpu.py` VRAM helpers.
- `ops.start` and `ops.swap` resolve the adapter once via `backends.for_kind(config.kind)` and call through it.

This slice should **not change observable behavior** — it's a refactor that puts every llama-server-specific call behind the adapter. Existing tests should pass without modification.

### Slice 19 — ModelConfig discriminated union

- `ModelConfig` becomes a tagged union or a base class with per-backend subclasses. Pydantic supports both via `discriminator="kind"`.
- Llama-server-specific fields (`mmproj_path`, `n_gpu_layers`, `flash_attn`, `no_mmap`) move to `LlamaServerConfig`.
- vLLM-specific fields (TBD: `tensor_parallel_size`, `gpu_memory_utilization`, `max_model_len`, `dtype`, ...) live on `VLLMConfig`.
- Common fields (`name`, `model_path`, `kind`, `ctx_size`, `np`, `extra_args`) stay on the base.
- `ConfigStore.from_dict_unvalidated` learns to pick the subclass off `kind`. Old configs without `kind` default to `llama_server`.

This is the breaking-shape change. Update the UI form (see slice 21) and the MCP `add_model` / `update_model_config` tools to take a `kind`-aware payload.

### Slice 20 — VLLMAdapter

- `llauncher/backends/vllm.py`.
- `build_command`: invokes `vllm serve <model> --port <port> --host 0.0.0.0 ...` plus `VLLMConfig` fields.
- `validate_model_path`: vLLM accepts HuggingFace IDs or local snapshots. Validation: if path looks like a local dir, check `config.json` + `tokenizer*` files; if it looks like an HF ID (`org/model`), defer (no offline validation).
- `estimate_vram_mb`: vLLM has its own VRAM math (KV-cache budget × `gpu_memory_utilization`). Probably pull from a published heuristic or vLLM's own estimator.
- `readiness_endpoint`: vLLM's `/health` differs from llama-server's `/health`; abstract via the adapter.
- `sentinel_kwargs`: env-var sentinel (per ADR-008 amendment) with `LAUNCHER_OWNED_MODEL=<name>` and `LAUNCHER_OWNED_PID=<self_pid>`. Used by `find_all_llama_servers`'s generalized successor.

### Slice 21 — UI + MCP surface

- UI add-model form learns a "Backend" radio/select. Per-backend fields render conditionally (Streamlit `st.expander` or a tabbed form).
- The "Models" tab's model card renders fields from the right `ModelConfig` subclass.
- MCP `add_model` schema becomes a discriminated union via JSON Schema `oneOf` on `kind`.
- HTTP `/models` payload picks up the `kind` field naturally (already there since M1 — verify).

### Slice 22 — Process discovery + sentinel

- Rename `find_all_llama_servers` → `find_all_owned_processes` (or similar).
- The function now reads `/proc/<pid>/environ` for `LAUNCHER_OWNED_*` instead of grepping argv. Cross-platform caveat: on Windows there's no `/proc`; we wrap behind a `read_process_env(pid)` abstraction.
- Lockfile remains the authoritative claim per ADR-008; the sentinel is for cross-validation and orphan detection (M5 item 4).

### Slice 23 — Amend ADRs 005, 006, 008

Each gets an Amendment Notes section dated 2026-MM-DD:

- **ADR-005 (model health)** — replace "GGUF check" with "backend-aware via adapter; GGUF is the LlamaServer adapter's implementation."
- **ADR-006 (VRAM monitoring)** — replace per-arch VRAM math with "per-adapter `estimate_vram_mb`."
- **ADR-008 (stateless facade)** — replace argv sentinel with env-var sentinel; cross-reference ADR-012.

## Touch Points

| Module | Change |
|--------|--------|
| `llauncher/backends/__init__.py` | **New** — Protocol + registry |
| `llauncher/backends/llama_server.py` | **New** — extract from `core/process.py`, `core/model_health.py`, VRAM helpers |
| `llauncher/backends/vllm.py` | **New** — slice 20 |
| `llauncher/models/config.py` | Discriminated union — slice 19 |
| `llauncher/core/process.py` | Reduce to dispatchers; add env-reading abstraction |
| `llauncher/core/model_health.py` | Reduce to dispatcher |
| `llauncher/core/gpu.py` | Reduce VRAM helper to dispatcher |
| `llauncher/operations/start.py`, `swap.py`, `preflight.py` | Resolve adapter from `config.kind` |
| `llauncher/ui/tabs/models.py` (post-M4) | Backend selector + per-kind form |
| `llauncher/mcp_server/tools/config.py` | `add_model` schema with `oneOf` on `kind` |
| `docs/adrs/012-backend-adapter-layer.md` | **New** |
| ADR-005, ADR-006, ADR-008 | Amendment Notes sections |

## Test Strategy

- **Unit tests per adapter** — each `BackendAdapter` method tested in isolation against fixture configs.
- **Adapter-registry test** — `for_kind(LLAMA_SERVER)` returns the right instance; unknown kind raises.
- **Discriminated-union round-trip tests** — `ModelConfig.from_dict({"kind": "vllm", ...})` produces a `VLLMConfig`; serialization preserves the discriminator.
- **Live integration test** for vLLM (`@pytest.mark.live`) — spin up a real small vLLM model on a test port. Skip if `vllm` isn't installed in the test environment.
- **Backwards-compat** — load a v1 `config.json` (no `kind` field) and verify it deserializes as `LlamaServerConfig`.

## Exit Criteria

- [ ] ADR-012 `Accepted`.
- [ ] ADRs 005, 006, 008 amended.
- [ ] `LlamaServerAdapter` and `VLLMAdapter` both pass their adapter-protocol test suites.
- [ ] No backend-specific code outside `llauncher/backends/`. Grep for `llama-server` and `gguf` in the rest of the tree returns only documentation/test-data.
- [ ] vLLM live integration test passes on a host with vLLM installed.
- [ ] UI lets a user add either a llama-server or vLLM model via the same flow.
- [ ] Old `config.json` (no `kind`) loads cleanly and runs llama-server unchanged.

## Estimate

**~3–5 sessions.** Slice 18 (extract) and slice 19 (discriminated union) are the structural lifts; slices 20–22 are mechanical follow-through; slice 23 is documentation.

## Open Questions

1. **vLLM-specific port/health idiosyncrasies?** vLLM exposes `/v1/models` and `/health`; llama-server exposes `/health` and `/props`. Adapter abstracts the readiness probe path. Verify vLLM doesn't need a longer warmup grace period (model load can take minutes for large vLLM models).
2. **Sentinel on Windows?** No `/proc/<pid>/environ`. Either:
   - (a) wrap behind `read_process_env(pid)` that uses `psutil.Process(pid).environ()` (cross-platform, requires elevated rights on some Win configurations);
   - (b) accept that orphan detection on Windows is "lockfile-only" with no env-var cross-check.
   Pin: (a). `psutil` is already a dependency.
3. **Should `BackendKind` be open-extensible?** I.e., can a user register a third-party adapter without forking llauncher? **Defer** — single-user hobby scope, no plugin system needed yet.
4. **vLLM's `--engine` flag (V1 vs V0)?** Pass through via `extra_args` initially. Promote to a first-class field if it becomes a regular knob.
5. **Multi-backend on same host — do they fight for VRAM?** Yes. The pre-flight VRAM check (ADR-006 + ADR-011) already considers all live processes; no special-casing needed beyond getting the per-adapter `estimate_vram_mb` right.

## References

- Issue [#42](https://github.com/shanevcantwell/llauncher/issues/42) — backend adapter layer (the source of truth for this design)
- Issue [#44](https://github.com/shanevcantwell/llauncher/issues/44) — VRAM estimator heuristic (closes alongside slice 20)
- ADR-005, ADR-006, ADR-008 — amended in slice 23
- prompt-prix `docs/ARCHITECTURE.md` — adapter-pattern reference
- v2-orientation-spike — sentinel discussion
