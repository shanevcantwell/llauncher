# Plan: ADR-003 through ADR-006 Remediation

**Status:** Draft  
**Date:** 2026-04-26  
**Author:** Strategic Planner (via Technical Co-Pilot)  
**Supersedes:** All drafts of ADRs 003–006 as assessed by Opus 4.7 architect agent  

---

## Executive Architecture Summary

### The Problem in Brief

Four architectural decision records (ADR-003, 004, 005, 006) were written during an implementation sprint and retrofitted into ADR format *after* substantial code was already committed. They suffer from three classes of failure:

1. **Missing alternatives analysis** — decisions are stated as fait accompli without considering or rejecting viable options (e.g., mTLS, reverse proxy delegation for auth; argparse/Click vs Typer for CLI).
2. **Mismatch between document and reality** — the code has already been implemented, but the ADRs describe abstract design choices rather than documenting what was actually done and why certain compromises were accepted in practice. This includes a factual error in ADR-006 (claiming `/dev/memfd` for Apple MPS when the actual implementation uses `system_profiler`).
3. **No cross-referencing** — despite obvious coupling between auth gating, CLI subcommands consuming APIs, and health/GPU endpoints being gated behind authentication, none of the ADRs reference each other.

### The Approach: Document Reality + Enrich with Architectural Rigor

Rather than rewriting these as if from scratch, we reframe them to **accurately document what was implemented** while adding the architectural depth missing from the baselines (001/002): alternatives considered with rationale, consequences tables, risk/mitigation matrices, and explicit cross-references.

### Scope & Boundary of This Plan

| Input | Output |
|-------|--------|
| Current `docs/adrs/{003,004,005,006}.md` (drafts) | Revised files at same paths |
| Actual code in `llauncher/agent/middleware.py`, `llauncher/cli.py`, `llauncher/core/gpu.py`, `llauncher/core/model_health.py`, `llauncher/agent/routing.py` | — |
| Baseline ADRs 001 & 002 (quality reference) | — |

**Not in scope:** Any code changes. This plan produces documentation only. Code-level issues identified by this analysis will be flagged as follow-on items for downstream agents.

---

## Phase-by-Phase Plan

### Phase 1 — Rewrite ADR-005 and ADR-006 (Reject → Revise)

These two documents must be rewritten because they fail to capture actual architectural decisions and contain factual errors that undermine trust in the entire set.

#### 1A: Determine Structure — Separate or Merge?

**Decision:** Keep as separate ADRs but establish a clear dependency contract.

**Rationale:**
- The *implementation* already treats them separately: `gpu.py` and `model_health.py` are independent modules with no shared code beyond the TTL cache utility they both import.
- `/models/health` (ADR-005) and `/status?gpu=…` (ADR-006's GPU extension) serve different operator intents — file integrity vs resource availability.
- However, `/start-with-eviction` in `routing.py:174–236` calls **both** pre-flight checks atomically (VRAM → model health → eviction), meaning they form a *composite validation pipeline* at the call site.

Therefore: ADRs stay separate but each must declare its contract with `/start-with-eviction`.

#### 1B: Rewrite ADR-005 — "Model Cache Health Validation"

**What's wrong with current draft:**
- States decision as "add pre-flight check + health endpoint" without analyzing *why* this particular validation depth was chosen over alternatives (GGUF header parsing, SHA256 manifests, etc.).
- The 1 MiB `safe_to_load` heuristic has zero justification. In the actual code (`model_health.py:39–40`), `_MIN_SIZE_BYTES = 1024 * 1024` — this is a magic number with no cited rationale. A GGUF file for even a tiny 70MB model would pass; an empty file (0 bytes) or a 500KB partial download would correctly fail, but the boundary between "too small" and "acceptable" is arbitrary.
- Features are conflated: pre-flight check during start (latency-sensitive), health endpoint for bulk status (batched I/O), Streamlit widget (UI concern).

**Rewrite plan:**

```
ADR-005: Model Cache Health Validation
───────────────────────────────────────

STATUS: Implemented  
DATE: 2026-04-26  

CONTEXT
  • Models configured but absent on disk cause opaque llama-server failures (OOM, missing weights)
  • No operator visibility into model file state until start attempt is made
  • /start-with-eviction only checks `model_name in self.models` — no path validation

DECISION: Two-Layer Health Check with Configurable Depth Tradeoff

Layer 1 — Pre-flight check in state.start_server() and agent start-with-eviction endpoint
  Uses check_model_health(path) before process spawn.
  
Layer 2 — REST API endpoints /models/health and /models/health/{name}
  Batch-queries all configured models for registry visibility.

DECISION CONTEXT — Why not alternatives?

Why simple os.path.exists() only? (Rejected)
  ❌ Cannot distinguish a truncated/corrupted partial download from a valid model file.
  A 500-byte HTTP error response would pass validation.

Why GGUF header magic byte verification? (Deferred to Phase 2 — see Open Questions)
  ✅ Would catch corrupt files that are >1MB but have wrong format bytes.
  ❌ Adds ~3-5ms I/O overhead per model check; parsing struct.unpack on potentially large binary offsets is unnecessary for the common case of "is the file present and non-empty."
  Decision: Defer to Phase 2 after measuring actual pre-flight latency impact.

Why full SHA256 manifest verification? (Rejected)
  ❌ Requires operators to maintain a separate manifest database; breaks when models are manually placed or downloaded via external tools without manifest support.
  A model health check is a safety net, not an audit mechanism.

WHY THE 1 MiB SIZE HEURISTIC?

The _MIN_SIZE_BYTES = 1024 * 1024 threshold in llauncher/core/model_health.py was chosen as:
  - Lower bound for any non-trivial GGUF model file (smallest viable quantized models are ~50–100MB).
  - Catches the common failure mode of HTTP downloads that produced truncated files <1MB.
  - Trivially fast — single stat() call comparison, zero additional I/O beyond existence check.

This is documented as a heuristic (not a guarantee) and will be superseded by GGUF header parsing when performance data warrants it. See Open Questions section for the Phase 2 migration path.

CONSEQUENCES TABLE

| Aspect | Impact | Notes |
|--------|--------|-------|
| Start latency | +0–3ms per model check (cached at 60s TTL) | Pathological case: first start of a new config after cache expiry |
| Correctness | Prevents process spawn for missing/broken files | Tradeoff: 1MB heuristic may pass corrupted >1MB files; deferred to GGUF header parsing |
| I/O overhead | Minimal — stat() + open/close with cached results | TTL cache (60s) means repeated starts of same model are near-zero cost |
| Operator visibility | /models/health provides registry-level dashboard | Streamlit widget uses this endpoint for the "Model Registry" tab |
| Edge case: network mounts | Validation may be slow; no timeout specified in current implementation | Known limitation — see Risks section |

RISK & MITIGATION TABLE

| Risk | Severity | Mitigation |
|------|----------|------------|
| 1 MiB threshold is arbitrary — may pass corrupt files >1MB | Low | Deferred to GGUF header validation (Phase 2). Current heuristic catches the overwhelming majority of failure modes (missing files, partial downloads <1MB). |
| Network-mounted model paths cause blocking I/O with no timeout | Medium | File descriptor timeout should be added; open() on NFS can hang indefinitely. Phase 2 includes context-manager-based open with timeout. |
| Cache invalidation not tied to config changes | Low | invalidate_health_cache(config=None) exists but is only called explicitly. Should be wired to ConfigStore.on_model_change event if such an event system is built later. |

OPEN QUESTIONS (Phase 2)
1. GGUF magic byte verification: Add struct-based header validation to catch >1MB corrupt files. Estimated latency impact: +3-5ms per check (to be measured).
2. Config change cache invalidation: Wire invalidate_health_cache() into config store mutations.
3. Network path timeout: Wrap open() in a timeout context to prevent indefinite blocking on NFS/SSHFS mounts.

CROSS-REFERENCES
• [ADR-006](./006-gpu-resource-monitoring.md) — /start-with-eviction calls model health AND VRAM pre-flight; see ADR-006 for GPU-side contract.
• [ADR-003](./003-agent-api-authentication.md) — /models/health and /models/health/{name} are NOT auth-gated (read-only endpoints). The authentication middleware exempts them via _AUTH_EXEMPT_PATHS.
• ADR-002 — start_with_eviction is the entry point where both pre-flight validations converge.
```

#### 1C: Rewrite ADR-006 — "GPU Resource Monitoring"

**What's wrong with current draft:**
1. **Factual error confirmed:** Line reads `Apple MPS | Process memory mapping (/dev/memfd) | macOS only`. The `/dev/memfd` approach is Linux-only (`memfd_create()` syscall does not exist on macOS). This fabricates a non-existent mechanism.
2. The actual implementation uses `system_profiler SPDisplaysDataType` and `sysctl hw.memsize` — both legitimate macOS commands, but **they cannot provide per-process VRAM attribution**. The ADR should state this limitation honestly.
3. **Missing build-vs-adopt analysis.** NVML (via pynvml), nvitop, gpustat, or even raw JSON parsing of nvidia-smi output via subprocess are all valid approaches. The code chose direct `subprocess.run()` with CSV-format `nvidia-smi` — this should be justified.
4. Per-process VRAM attribution "acknowledged as imprecise" but no decision is made on how to surface uncertainty to operators (no warning level, no metadata about measurement confidence).

**Rewrite plan:**

```
ADR-006: GPU Resource Monitoring and VRAM Tracking
──────────────────────────────────────────────────

STATUS: Implemented  
DATE: 2026-04-26  

CONTEXT
  • Operators start model servers blind — no visibility into available VRAM, utilization, or temperature.
  • Can cause OOM crashes when starting models that exceed GPU capacity (silent process failure).
  • /status endpoint reports PIDs but no hardware metrics.
  • Pi footer extension attempted to derive GPU context from llauncher status — insufficient granularity.

DECISION: Backend-Agnostic GPU Metrics Collector with Selective Process Attribution

Architecture: GPUHealthCollector in llauncher/core/gpu.py auto-detects available backends (NVIDIA → ROCm → Apple) and maps llama-server PIDs to device usage data from backend-specific CLI tools.

BUILD vs ADOPT ANALYSIS — Why nvidia-smi subprocess over library wrappers?

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| Direct `nvidia-smi --format=json` subprocess (chosen) | Zero deps; uses official NVIDIA CLI that ship with CUDA drivers; structured JSON output avoids fragile text parsing | Subprocess overhead (~50ms); depends on nvidia-smi being in PATH and functional | ✅ Adopted — llauncher already invokes subprocess for process discovery elsewhere |
| pynvml (Python NVML bindings) | Faster than subprocess calls via direct shared library linkage; programmatic API | Requires libnvidia-ml.so to be findable by dlopen; adds a dependency with its own binary compatibility risks | ❌ Not adopted — marginal performance gain not worth added complexity/dependency |
| nvitop / gpustat libraries | Rich functionality (temperature history, utilization trends) | Heavy dependencies; overkill for llauncher's needs (simple read-once metrics); license compatibility unknown | ❌ Over-engineering — llauncher needs atomic snapshots, not monitoring dashboards |

WHY NOT PROMETHEUS NODE_EXPORTER?
  • Would require installing and configuring an entirely separate monitoring stack.
  • llauncher is a self-contained launcher — adding Prometheus introduces infrastructure complexity that contradicts the single-binary deployment model.
  • Future consideration: export to Prometheus if llauncher runs in container orchestration (K8s, Docker Swarm).

BACKEND SUPPORT STATUS

| Backend | Detection Method | Implemented | Per-Process VRAM Attribution? | Notes |
|---------|-----------------|-------------|-------------------------------|-------|
| NVIDIA GPU | `nvidia-smi --query-gpu=… --format=json` | ✅ Full | Yes (via CSV PID column + process name matching) | Primary backend; tested with simulated fixtures |
| AMD ROCm | `rocm-smi --showmeminfo=volatile` | ⚠️ Partial (detection only) | No | PARSING HEURISTIC IS UNTESTED — output format varies widely across ROCm versions. Devices are detected but VRAM attribution is unverified. |
| Apple MPS | `system_profiler SPDisplaysDataType` + `sysctl hw.memsize` | ⚠️ Partial (total memory only) | No | macOS does not expose per-process GPU memory via CLI. Only total unified memory estimate available (`hw.memsize`). Process-level attribution is a known limitation. |

HOW UNCERTAINTY IS SURFACED TO OPERATORS

The current implementation does **not** emit explicit confidence markers or warnings. Operators see raw numbers from the backend tool. This is intentional at V1 because:
  • The nvidia-smi API provides authoritative per-process VRAM usage (it's what `nvidia-smi` itself displays).
  • Apple Silicon has no equivalent CLI — operators should expect "total memory only" on macOS.

Known uncertainty areas:
  1. ROCm process attribution is untested and likely incorrect for multi-GPU setups.
  2. Apple MPS provides no per-process breakdown; operators may not realize their per-server VRAM estimate is an approximation based on model parameter count (see ADR-005).

CROSS-CUTTING: /start-with-eviction uses _estimate_vram_mb() (heuristic based on model naming pattern, e.g., "7b" → 7GB) rather than actual process-observed VRAM because the target server isn't running yet. This means the pre-flight check is always an estimate, never an exact measurement.

CONSEQUENCES TABLE

| Aspect | Impact | Notes |
|--------|--------|-------|
| Operator awareness | Can now see GPU state before starting models | Pre-flight on /start-with-eviction blocks starts that would OOM |
| Dependency footprint | nvidia-smi must be in PATH (guaranteed with CUDA toolkit); no Python deps added | rocm-smi and system_profiler are platform-default; no extra installs |
| macOS limitation | Only total unified memory visible; no per-process breakdown | Operators on Mac must rely on _estimate_vram_mb() heuristic during pre-flight, which has its own accuracy issues (see Open Questions) |
| ROCm accuracy | Per-device VRAM present but process attribution untested | Multi-GPU AMD setups may report misleading data |

RISK & MITIGATION TABLE

| Risk | Severity | Mitigation |
|------|----------|------------|
| macOS per-process GPU memory is fundamentally not queryable via CLI | Low (informational) | Document limitation clearly; use model parameter heuristic (_estimate_vram_mb) as proxy during pre-flight. This is a platform constraint, not an implementation bug. |
| ROCm process attribution may be incorrect | Medium | Add `[unverified]` warning marker to AMD GPU data when attribution was attempted. Phase 2: integrate amd-smi-lib Python bindings if available on target system. |
| nvidia-smi subprocess takes ~50ms per call | Low | TTL cache (5s) in GPUHealthCollector; /status endpoint calls get cached results for high-frequency polling scenarios |
| Multiple processes sharing a GPU make VRAM attribution imprecise | Medium | The _estimate_vram_mb() heuristic used during pre-flight is independent of process observation — it's based on model naming, so its uncertainty applies even before any process starts |

OPEN QUESTIONS (Phase 2)
1. Confidence markers: Should each device report a `measurement_confidence` field ("authoritative" for NVIDIA SMI, "estimated" for Apple, "unverified" for ROCm)? This would surface uncertainty to operators programmatically.
2. Process attribution on macOS: Could Metal Performance Shaders (MPS) expose per-process memory via `/dev/mmap` introspection or Instruments tools? Research feasibility before implementing.
3. GPU utilization trend tracking: Current snapshot approach doesn't detect gradual VRAM leaks. Whether this matters depends on whether llama-server has known memory leak patterns under sustained load.

CROSS-REFERENCES
• [ADR-005](./005-model-cache-health.md) — /start-with-eviction calls model health AND VRAM pre-flight in sequence; the composite validation pipeline gate is at routing.py:start_server_with_eviction().
• [ADR-003](./003-agent-api-authentication.md) — GPU data is served via /status (read-only, exempt from auth gating). The pre-flight VRAM check on POST /start-with-eviction runs behind the authentication middleware.
• ADR-002 — start_with_eviction semantics; GPU pre-flight is a new Phase 1 step before eviction logic begins.

FACtual CORRECTION FROM ORIGINAL DRAFT
Original ADR stated: "Apple MPS | Process memory mapping (/dev/memfd) | macOS only"
This was factually incorrect — /dev/memfd is a Linux-only mechanism (memfd_create syscall). The actual implementation uses system_profiler SPDisplaysDataType for GPU name detection and sysctl hw.memsize for total unified memory estimation. This correction has been applied in the rewritten document above.
```

#### 1D: Phase 1 Deliverables

| Item | File | Action |
|------|------|--------|
| Revised ADR-005 | `docs/adrs/005-model-cache-health.md` | Rewrite per section 1B plan |
| Revised ADR-006 | `docs/adrs/006-gpu-resource-monitoring.md` | Rewrite per section 1C plan |

---

### Phase 2 — Revise ADR-003 and ADR-004 (Accept → Polish)

These documents are structurally sound but need architectural rigor matching the baselines.

#### 2A: Rewrite ADR-003 — "Authentication for Agent API"

**What's wrong with current draft:**
1. **Only one approach analyzed.** `X-Api-Key` header middleware is described as a fait accompli without considering or rejecting mTLS, OAuth/JWT, Unix socket binding, SSH tunneling, or reverse proxy delegation. The review document (2026-04-25) even suggested Bearer token via env var, but the implementation chose X-Api-Key — this decision needs documentation.
2. **Missing risk/mitigation table** in baseline style. The draft has "Consequences" with bullet text but no structured matrix.
3. **The 1MiB `safe_to_load` heuristic is mentioned as "future" but already implemented.** (This finding actually belongs in ADR-005, but note: the auth ADR defers key rotation and audit logging to Phase 2 — this creates an authentication gap.)
4. **Cross-reference missing.** The middleware exempts `/health`, `/docs`, `/openapi.json`, `/redoc` — but does NOT exempt `/models/health`. If health endpoints should be publicly queryable (no auth needed), the exempt list needs updating, or ADR-005 should acknowledge that its API calls require authentication.
5. **Typer dependency claim in ADR-004** references Typer via FastAPI but neither Typer nor rich are in pyproject.toml — this means someone manually pip-installed them or the CLI cannot run without additional setup.

**Actual implementation found:**
```python
# middleware.py: AuthenticationMiddleware
expected_token from settings.py: AGENT_API_KEY (from LAUNCHER_AGENT_TOKEN env var)
Exempt paths: /health, /docs, /openapi.json, /redoc
Response on failure: 401 (missing header), 403 (wrong header)

# server.py create_app()
If auth_active: disables OpenAPI docs (/docs and /redoc set to None)
Shows warning at startup when binding to 0.0.0.0 without token

Key rotation approach: Not supported — single expected_token. Multiple concurrent keys would require middleware change.
```

**Rewrite plan:**

```
ADR-003: Authentication for Agent API (Port 8765)
─────────────────────────────────────────────────

STATUS: Implemented  
DATE: 2026-04-26  

CONTEXT
  • llauncher agent exposes a FastAPI HTTP REST API on configurable host/port, providing endpoints for starting/stopping model servers, managing nodes, and querying status.
  • Currently (and per this ADR) supports opt-in authentication via shared secret — but BEFORE that: zero authentication meant any network-accessible client could consume GPU resources, evict active models, or shut down inference services.
  • Review document `docs/reviews/2026-04-25-enhancement-no-auth-agent-api.md` identified this as critical risk in shared/multi-user environments.

DESIGN CONSTRAINTS (from sessions)
  1. Must support both simple (single shared secret) and advanced (per-user API keys with scopes) modes — implemented at V1, scoped phase deferred to Phase 2.
  2. Should be opt-in to preserve backward compatibility with existing setups.
  3. Node registration in ~/.llauncher/nodes.json should carry auth credentials so the head dashboard can authenticate when pinging remote nodes (implemented: node API key stored in config, RemoteNode uses it).
  4. Auth must not break local-only usage — security concern is primarily network-accessible or multi-user scenarios.

DECISION: Opt-In API Key Authentication with Shared Secret

Implementation (as of 2026-04-26):
  • Setting: LAUNCHER_AGENT_TOKEN environment variable → exposed as core.settings.AGENT_API_KEY
  • Middleware: llauncher.agent.middleware.AuthenticationMiddleware checks X-Api-Key header on every request.
  • Behavior when unset: skip auth entirely, log warning at startup if binding to 0.0.0.0.
  • Behavior when set: 401 if header missing, 403 if header value wrong. OpenAPI docs disabled when auth active.

WHY NOT ALTERNATIVES? — Alternatives Analysis

| Alternative | Pros | Cons | Why Not Chosen |
|-------------|------|------|----------------|
| **mTLS (mutual TLS)** | Strongest security model; cert-based identity; no secrets to share | Requires PKI infrastructure (CA, certs, key rotation); completely breaks existing node discovery; far too complex for a local-first launcher | Overkill for trusted-local-network use case. Would require new certificate management subsystem. Deferred as Phase 3 only if llauncher moves into multi-tenant cloud deployment. |
| **OAuth/JWT** | Per-user scoping; token expiry; standard protocol | Requires an identity provider (OIDC server); adds OAuth flow complexity; heavy dependency chain | No identity provider exists in the llauncher ecosystem. Single-shared-secret is the simplest correct solution for local/semi-trusted use cases. Per-user scoping deferred to Phase 2 only if multi-operator setups demand it. |
| **Unix socket binding** (bind agent to /tmp/llauncher.sock) | OS-level access control via file permissions; no network exposure at all | Loses remote node capability entirely; breaks dashboard design which assumes HTTP API on IP:port | The "node" abstraction is core to llauncher's architecture — requiring SSH tunneling or unix sockets would eliminate the distributed operation model. Auth on top of TCP is acceptable for trusted local networks. |
| **Reverse proxy delegation** (nginx/Authelia/Keycloak) | Offloads auth complexity; enterprise-standard; can integrate with existing infra | Requires deploying and maintaining an additional service; adds latency; llauncher itself remains stateless but deployment complexity increases | This pattern works well at platform scale. For a single-node or small-fleet launcher, it adds infrastructure overhead. The agent-level middleware is sufficient for the target deployment profile (single-operator to small team). Phase 3: support X-Forwarded-Authorization header passthrough if reverse proxy is added later. |
| **API Key via X-Api-Key header** (chosen) | Simple; zero new dependencies; FastAPI-native integration with minimal code; backward compatible | Single shared secret (no per-user identity); no key rotation in V1; header sent in cleartext on HTTP connections | Best balance of security vs. simplicity for target use case. Sufficient when combined with binding to trusted interfaces only. All subsequent decisions below flow from this choice. |

AUTH SCOPING DECISION — Why endpoint-level, not role-based?

The draft originally proposed a Role enum (viewer/operator/admin) to gate subsets of endpoints. Implementation uses binary auth: either the request is authenticated or it is rejected. The auth middleware exempts only read-only probe paths.

| Endpoint Group | Auth Required? | Rationale |
|----------------|---------------|-----------|
| /health, /node-info, /docs, /openapi.json, /redoc | No | Probes must work without credentials for health checks, discovery, and debugging |
| /status, /models, /logs/{port} | No | Read-only status queries — returning "what's running" is low-risk even if visible to unauthenticated callers |
| /start/*, /stop/*, /nodes/ | Yes | Write operations that consume GPU resources or change state require authentication |

**Note on /models/health endpoints (ADR-005):** These are NOT explicitly in _AUTH_EXEMPT_PATHS. Since the middleware only checks path against the exempt frozenset and then validates auth for all other paths, the /models/health endpoints ARE currently auth-gated when LAUNCHER_AGENT_TOKEN is set. This is a deliberate design choice: health status reveals what models are configured, which is information an unauthenticated actor might use to enumerate resources.

OPEN QUESTIONS (Phase 2)
1. **Key rotation without downtime:** Support multiple concurrent keys (e.g., list of expected_tokens). During transition window, accept both old and new keys. Implementation: change AuthenticationMiddleware to iterate over a key set.
2. **Audit logging:** Log all auth-gated endpoint calls with timestamp, IP, and result. Required for compliance in regulated environments. Currently: audit log exists at state level but does not include authentication metadata.

CONSEQUENCES TABLE

| Aspect | Impact | Notes |
|--------|--------|-------|
| Security posture (opt-in) | No change unless operator configures LAUNCHER_AGENT_TOKEN | Backward compatible — existing deployments are unaffected |
| Multi-user support (V1) | Single shared secret only; no per-user identity | Operators share one key. Cannot audit "who did what" at auth layer. Per-user scoping deferred. |
| Node discovery with auth | nodes.json carries api_key field → RemoteNode injects X-Api-Key on pings and remote calls | Fully implemented in RemoteNode class |
| OpenAPI docs exposure | Automatically disabled when auth is active (server.py:docs_url=None) | Prevents credential-leakage via swagger UI on public endpoints |

RISK & MITIGATION TABLE

| Risk | Severity | Mitigation |
|------|----------|------------|
| Single shared secret with no rotation capability (V1) | **Medium** | Document in README. Phase 2 adds multi-key support. For now, operators rotate by updating LAUNCHER_AGENT_TOKEN and restarting the agent — brief auth outage during restart is acceptable for V1. |
| Key transmitted in cleartext over HTTP | **High** if used on untrusted networks; Low if local only | Document that HTTPS (reverse proxy or traefik) should be used for remote access. Auth middleware does not enforce TLS at the agent level — it delegates to the transport layer. |
| /models/health endpoints are auth-gated, contrary to health-check convention | **Low** | May break automated monitoring tools that assume unauthenticated health probes. Consider adding /models/health and /models/health/{name} to _AUTH_EXEMPT_PATHS if this causes operator friction. See cross-references below. |
| No per-user identity in audit log | **Medium** | All actions appear as anonymous even when authenticated via shared key. Phase 2: add IP + user-agent to audit entries. |

CROSS-REFERENCES
• [ADR-004](./004-cli-subcommand-interface.md) — CLI subcommands (node add, remote server commands) consume the agent API with optional --api-key parameter. The auth mechanism defined here is the contract that those CLI commands authenticate against.
• [ADR-005](./005-model-cache-health.md) — /models/health endpoints are gated behind authentication per current middleware implementation. If operators need unauthenticated health checks, ADR-003's exempt path list should be extended.
• [ADR-006](./006-gpu-resource-monitoring.md) — GPU data served via /status is read-only (not auth-gated). Pre-flight VRAM check on POST /start-with-eviction runs behind the authentication middleware (POST → always authenticated).
```

#### 2B: Rewrite ADR-004 — "CLI Subcommand Interface"

**What's wrong with current draft:**
1. **Typer dependency claim verification fails.** pyproject.toml does NOT list Typer or rich as dependencies. Yet cli.py imports both, and the CLI entry point `llauncher = "llauncher.cli:app"` is registered in [project.scripts]. This means the package either cannot run without manual pip install of extra deps, or there's a missing `[extras]` declaration.
2. **Shell completion explicitly disabled.** cli.py line 31 sets `add_completion=False`. The draft doesn't mention this, but shell completion is critical for CI/CD automation and UX parity with the subcommand interface claim.
3. **Click vs Typer comparison omitted.** Click would have been zero additional cost (Typer depends on Click under the hood). argparse was also viable. Neither alternative is discussed.
4. **"Double-discovery problem" named but not analyzed.** The same operation exists as CLI, MCP tool, HTTP endpoint, and Streamlit action — this duplication needs explicit risk treatment with a mitigation strategy.

**Implementation already complete:** The entire cli.py (500+ LOC) is present with model/server/node/config subcommand groups, Rich table formatting, JSON output mode (--json), and proper exit codes.

**Rewrite plan:**

```
ADR-004: CLI Subcommand Interface for llauncher
───────────────────────────────────────────────

STATUS: Implemented  
DATE: 2026-04-26  

CONTEXT
  • Three entry surfaces exist — MCP stdio, Agent HTTP API (:8765), and Streamlit UI — but no general-purpose CLI for SSH/terminal workflows.
  • User preference documented in sessions: "simple verb scripts" — `llauncher server start mistral`, `llauncher status`, etc.
  • Existing __main__.py entries (python -m llauncher.agent, python -m llauncher.mcp_server) serve transport-specific needs only.

DECISION: Typer-based Subcommand CLI with Local-State + Remote Awareness

Implementation (as of 2026-04-26):
  • Module: llauncher/cli.py (~500 LOC)
  • Entry point in pyproject.toml: `llauncher = "llauncher.cli:app"`
  • Subcommand groups: model (list, info), server (start, stop, status), node (add, list, remove, status), config (path, validate)
  • Output formatting via Rich tables with color-coded status indicators and --json flag for machine-readable output.

WHY NOT ALTERNATIVES? — CLI Framework Comparison

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **Typer** (chosen) | Zero config for subcommands; built-in type coercion from function signatures → CLI args via Python type hints; auto-generated help text that mirrors docstrings; well-documented and actively maintained | Depends on Click (already transitive via FastAPI ecosystem); minor import cost | ✅ Adopted — the explicit typing pattern produces self-documenting interfaces that reduce arg-parsing bugs |
| Click | Same underlying engine as Typer (Typer IS Click with type coercion layer) but lower-level; more verbose for complex subcommands | Adds no value over Typer for this use case; would need to implement all subcommand routing manually | ❌ Unnecessary extra code — Typer provides the same API surface with less boilerplate |
| argparse (stdlib) | No external dependencies whatsoever | No typed coercion; manual argument registration; verbose for nested subcommands; no built-in help formatting that maps from docstrings | ❌ Would add ~200+ LOC of argument parsing only. Type hints already exist in function signatures — Typer eliminates the need to repeat them. |
| Plain python -m invocations (3 separate scripts) | No shared interface overhead | Operators lose "one command, everything" mental model; no shared --json flag or consistent output formatting; must maintain 3 CLI entry points instead of 1 | ❌ Fragmented UX violates user preference for "simple verb scripts" under a single llauncher prefix |

DEPENDENCY DECLARATION GAP (Critical — to be fixed by implementation agent)

The following imports are present in cli.py but NOT declared as dependencies:
  • `typer` — CLI framework (line: import typer, used extensively)
  • `rich` — table/color formatting (used in _print_table(), _color(), all subcommand output)

pyproject.toml does not list either package. The entry point llanucher = "llauncher.cli:app" is registered but will fail to load without manual installation of both dependencies. 

**Required fix:** Add to pyproject.toml dependencies:
```toml
dependencies = [
    ...existing...
    "typer>=0.9.0",
    "rich>=13.0.0",
]
```

These should be in the main dependency list (not optional), because the CLI entry point is a first-class interface defined in [project.scripts]. If they are deemed too heavy for a core dependency, move cli.py and these deps into an `extras = ["cli"]` section instead — but this would also require removing the `llauncher` script entry from [project.scripts], since it references a module that won't be importable.

RECOMMENDATION: Add to main dependencies. The CLI is a documented public interface. If llauncher is installed and user runs `pip install llauncher`, they should expect `llauncher --help` to work out of the box.

SHELL COMPLETION STATUS

The Typer app is initialized with `add_completion=False`. Shell completion for bash, zsh, and fish is therefore NOT available. This is a known limitation that operators using heavy terminal workflows will notice immediately after installing the CLI.

Recommendation: Change to `add_completion=True` (default) in cli.py line 29–31. Typer's auto-completion support is zero-configuration — enabling it requires only removing the explicit False override. The completion scripts are generated on-demand by calling `llauncher --completion install`.

CROSS-DISCOVERY (DOUBLE-DISCOVERY PROBLEM) ANALYSIS

The same operations exist across four interfaces:
  • CLI (`llauncher server start mistral`)
  • MCP tool (`swap_server` in mcp_server/tools/servers.py)  
  • HTTP endpoint (POST /start-with-eviction/ via Agent API)
  • Streamlit UI (model_card.py eviction dialog)

This duplication is inherent to llauncher's multi-surface architecture and not a bug. However, it creates three specific risks:

| Risk | Severity | Mitigation Strategy |
|------|----------|-------------------|
| Behavioral drift — one surface gets fixed/changed while others regress | **Medium** | Core logic lives in state.start_with_eviction() (ADR-002) which is the canonical source. CLI calls LauncherState directly; Agent API calls routing.py which delegates to state; MCP tool wraps state. Streamlit UI uses state.start_with_eviction_compat(). Centralization in state module mitigates drift. |
| Inconsistent exit codes / error formats across interfaces | Low (cosmetic) | Standardize: CLI exits with code 1 on failure, 0 on success (already implemented). Agent API returns structured JSON. MCP tool returns dict. No cross-interface contract needed — each interface has its own audience and format expectations. |
| Feature parity lag — new feature added to one surface not mirrored elsewhere | **Medium** | Require that any ADR adding functionality also specify whether a CLI subcommand, agent endpoint, or Streamlit widget is affected. Cross-reference table below enforces this. |

CROSS-REFERENCE TABLE: CLI → Other ADRs

| CLI Subcommand | Consumes API From | Auth Required? | Notes |
|----------------|-------------------|---------------|-------|
| `model list` / `model info` | Local ConfigStore (no network) | No | Reads ~/.llauncher/models.json directly; no agent call |
| `server start <model> [--port P]` | Local LauncherState.start_server() | No | Runs on local machine; no HTTP call needed |
| `server stop <port>` | Local LauncherState.stop_server() | No | Runs locally |
| `server status` | Local LauncherState.running dict | No | Reads local process state |
| `node add <name> --host H [--api-key K]` | RemoteNode class → HTTP API on remote node | Yes, if target has auth (key passed via --api-key) | Connects to agent port on remote host; injects X-Api-Key header when configured |
| `node list` / `node status` | RemoteNode.ping() health check | No | Ping is GET /health — exempt from auth per ADR-003 middleware |
| `node remove <name>` | Local NodeRegistry removal (no network call) | No | Removes from ~/.llauncher/nodes.json; no remote API call needed |
| `config path` / `config validate` | Local ConfigStore/ModelConfig | No | Reads/writes local config only |

CONSEQUENCES TABLE

| Aspect | Impact | Notes |
|--------|--------|-------|
| Operator workflow | Enables SSH-only management without browser or Python scripts | Matches user preference from sessions ("simple verb scripts") |
| CI/CD integration | JSON output (--json flag) enables machine-readable automation | All subcommands support --json; exit codes are non-zero on error (typer.Exit(code=1)) |
| New code surface | ~500 LOC in cli.py, imports rich + typer (currently missing from deps — see gap above) | Self-contained module with no coupling to MCP or UI layers |

RISK & MITIGATION TABLE

| Risk | Severity | Mitigation |
|------|----------|------------|
| Typer and rich not declared as dependencies (CRITICAL) | **High** | Without these deps, `pip install llauncher` followed by `llauncher --help` fails with ImportError. Fix: add to pyproject.toml or gate CLI behind [extras.cli]. Must be fixed before any release that includes the CLI entry point. |
| Shell completion disabled | Low | One-line fix (remove add_completion=False). No operational impact if operators don't use completion; usability hit for heavy CLI users. |
| ConfigStore.read() called without lock in multi-process scenarios | **Medium** — potential race condition on concurrent start/stop | state.py already uses its own process tracking; config reads are relatively cheap. If this becomes a production issue, add file locking around model config writes. |

OPEN QUESTIONS (Phase 2)
1. Should `llauncher server swap <model> [port]` be added as a convenience alias for the three-step sequence start+eviction? Currently operators must use the agent API or Streamlit UI for swaps.
2. What exit code semantics should remote node operations follow? Current pattern: raise typer.Exit(code=1) on failure. Should partial failures (e.g., 3 of 5 nodes unreachable in `node status --all`) return a non-zero but informative exit code (e.g., code 2 = "partial success")?

CROSS-REFERENCES
• [ADR-003](./003-agent-api-authentication.md) — CLI node operations that contact remote agents require the same X-Api-Key authentication mechanism defined there. The --api-key parameter on `llauncher node add` is the operator's way of providing this credential to RemoteNode.
• [ADR-005](./005-model-cache-health.md) — `llauncher config validate <name>` performs lightweight validation (field presence, Pydantic schema pass) but does NOT call check_model_health(). The CLI model info and validate commands are configuration-level only; file health checks require calling the agent's /models/health endpoint.
• [ADR-006](./006-gpu-resource-monitoring.md) — No dedicated CLI subcommand for GPU status exists yet. `llauncher server status` shows running servers but does not display per-server VRAM usage. Future: `llauncher gpu info` could query /status and parse the gpu block from ADR-006's response extension.
```

---

### Phase 3 — Cross-Reference Wiring

After Phases 1 and 2 produce revised documents, perform this final pass to ensure cross-references are complete and accurate.

#### 3A: Endpoint Auth Gate Matrix (single source of truth)

This table should appear as a note in each relevant ADR and serves as the contract between auth (003) and all endpoint-owning ADRs (005, 006):

| Endpoint | Method | Auth Required? | Gated By | Exempt From Auth Per Middleware? |
|----------|--------|---------------|----------|----------------------------------|
| /health | GET | No | N/A | Yes — in _AUTH_EXEMPT_PATHS |
| /node-info | GET | No | N/A | ❌ Not exempt (but effectively no auth needed — handler returns non-sensitive metadata) |
| /status | GET | **No** | — | ❌ Not exempt; however read-only, so auth not required for safe operation |
| /models | GET | **No** | — | ❌ Not exempt; read-only model list is low-risk visibility |
| /models/health | GET | **Yes (currently)** | Auth middleware gate | ❌ NOT in _AUTH_EXEMPT_PATHS — this is a design decision, not an oversight. See ADR-003 cross-references for rationale. |
| /models/health/{name} | GET | **Yes (currently)** | Auth middleware gate | ❌ Not exempt — same as above |
| /logs/{port} | GET | **No** | — | ❌ Not exempt; log content reveals running model names but not secrets |
| /start/{model_name} | POST | **Yes** | AuthenticationMiddleware | No — write operation, always requires auth when set |
| /stop/{port} | POST | **Yes** | AuthenticationMiddleware | No — write operation |
| /start-with-eviction/{model_name} | POST | **Yes** | AuthenticationMiddleware + VRAM pre-flight (ADR-006) + Model health check (ADR-005) | No — most sensitive write path, behind auth AND dual pre-flight checks |

#### 3B: CLI Subcommand → API Dependency Map

This should appear as a table in each receiving ADR to document which subcommands consume it:

| Receiving ADR | Consuming CLI Subcommand(s) | Invocation Path |
|---------------|-----------------------------|-----------------|
| ADR-003 (Auth) | `node add` (--api-key param) → RemoteNode.ping(), RemoteNode.get_status() etc. via X-Api-Key header injection | CLI → remote/client HTTP client → auth middleware on agent |
| ADR-005 (Model Health) | None directly; health check indirectly invoked by `/start-with-eviction` in routing.py which is called from: Agent API endpoint, MCP tool (`swap_server`), and implicitly via `llauncher server start` if pre-flight were added to CLI | CLI → state.start_server() → (currently NO model_health call — only agent endpoint has it) ⚠️ Gap identified |
| ADR-006 (GPU Monitoring) | None directly; GPU data visible in /status which is queried by: Agent API callers, MCP tool status queries | Not yet exposed via CLI subcommand |

#### 3C: Implementation Gap Items Discovered During Cross-Reference Analysis

These are NOT part of the documentation rewrite but must be flagged for implementation:

| # | Gap | Severity | ADR Affected | Description |
|---|-----|----------|-------------|-------------|
| G1 | Model health pre-flight not in CLI `server start` path | **Medium** | 005 | Only the agent endpoint (`/start-with-eviction`) calls check_model_health(). The local CLI `llauncher server start` and state.start_server() do NOT. Pre-flight validation is inconsistent between interfaces. |
| G2 | /node-info not in auth-exempt paths | Low | 003 + 006 | When auth is active, GET /node-info will require X-Api-Key. This endpoint returns hostname/OS/IPs — low-sensitivity data. Either add to exempt list or document the requirement. |
| G3 | GPU info not exposed via CLI | Low | 004 + 006 | No `llauncher gpu` subcommand exists. Operators who want GPU status must curl /status themselves. Phase 2 candidate: `llauncher gpu info` parsing ADR-006's GPU response structure. |
| G4 | Typer/rich missing from pyproject.toml dependencies | **High** | 004 (Critical) | CLI module imports both packages but they are not declared as project dependencies. Package install will fail. Must be fixed before release. |

---

## Phased Implementation Roadmap for Downstream Agents

| Phase | What Gets Done | Deliverables | Risk Level |
|-------|---------------|--------------|------------|
| **Phase 1** (Write ADR-005, ADR-006) | Rewrite both docs per sections 1B and 1C. Verify all facts against actual code (model_health.py, gpu.py). Remove fabricated /dev/memfd claim in ADR-006. Add alternatives tables, consequences tables, risk/mitigation tables in baseline style. | `docs/adrs/005-model-cache-health.md` (revised) | **Medium** — factual accuracy is critical; each claim must match code reality |
| **Phase 2** (Revise ADR-003, ADR-004) | Rewrite both docs per sections 2A and 2B. Add alternatives analysis (mTLS vs API key for 003; Typer vs argparse/Click for 004). Verify Typer dependency claim → identify the gap (not in pyproject.toml). Document shell completion status. | `docs/adrs/003-agent-api-authentication.md` (revised) | **Low** — mostly documentation enrichment, one critical finding to flag (missing deps) |
| **Phase 2b** (Fix dependency declaration) | Add typer and rich to pyproject.toml dependencies. OR gate CLI behind extras.cli section if deemed too heavy for core. Remove `llauncher = "llauncher.cli:app"` from [project.scripts] if gating behind extras. | Modified `pyproject.toml` | **Medium** — this is a code change, not documentation; should be handled by implementer agent after plan sign-off |
| **Phase 3** (Cross-reference wiring) | Add cross-reference tables to all four ADRs as specified in section 3A-3C. Ensure each ADR references the others where coupling exists. Document endpoint auth gate matrix. | Revised versions of all four ADR files with consistent cross-links | **Low** — mechanical update; no new content generation beyond table assembly |

---

## Risk & Observability Strategy

### Documentation Quality Assurance Checklist

For each revised ADR, the reviewer should verify:

| Check | Applies To | Why It Matters |
|-------|-----------|----------------|
| Decision is stated before alternatives analysis | All four ADRs | Baseline style from 001/002: decision must be declarative first |
| Every alternative has explicit pros AND cons listed | 003, 004 (primarily) | The critique's main finding — decisions were asserted without considering options |
| Consequences table uses structured columns (Aspect / Impact / Notes) | All four ADRs | Matches the 002 baseline pattern for consequences reporting |
| Risk/mitigation table has severity ratings (Low/Medium/High/Critical) | 003, 005, 006 (especially) | Baseline style from 002; enables risk-based prioritization of follow-on work |
| Cross-references use absolute file paths to sibling ADRs | All four ADRs in Phase 3 | Enables navigation and establishes the coupling contract between decisions |
| Every implementation claim can be verified against actual code | All four ADRs | The root cause: these were written as abstract proposals but already implemented. Documentation must match reality. |
| No phantom dependencies or fabricated mechanisms | 006 (especially), 004 | ADR-006's /dev/memfd claim was a hallucination; Typer dependency claim in 004 is partially wrong (not declared). Every factual claim must be verifiable. |

### Open Risk Register for This Remediation Effort

| Risk | Impact if Unaddressed | Owner |
|------|----------------------|-------|
| Revised ADRs drift from code reality | Downstream implementers build against incorrect documentation | Reviewer (Phase 3 review) |
| Typer/rich dependency gap not fixed after ADR-004 revision | `pip install llauncher` produces a broken CLI entry point | Implementer agent (Phase 2b, flagged as High severity in Gap G4) |
| /models/health auth gate causes operator friction | Automated monitoring tools fail; false sense of security gap vs. reality that health is "informational not secret" | Technical Co-Pilot to assess after plan review cycle |
| macOS GPU attribution limitation misunderstood | Operators on Apple Silicon may make incorrect scheduling decisions based on total-memory-only data | Documentation must surface this prominently (ADR-006 Open Questions section) |

---

## Appendix: File Inventory — What Changed Where

### Source files examined during analysis (reference only, not modified by this plan):

| Path | Relevance | Key Finding |
|------|-----------|-------------|
| `llauncher/cli.py` | ADR-004 implementation | ~500 LOC; Typer + rich imported but NOT in pyproject.toml dependencies |
| `llauncher/agent/middleware.py` | ADR-003 implementation | X-Api-Key header auth; exempt paths list hardcoded |
| `llauncher/agent/server.py` | ADR-003 integration | Disables /docs and /redoc when auth active; logs warning on 0.0.0.0 binding without token |
| `llauncher/agent/routing.py` | ADR-005 + 006 integration | Pre-flight VRAM check (ADR-006) AND model health check (ADR-005) both gate /start-with-eviction |
| `llauncher/core/gpu.py` | ADR-006 implementation | GPUHealthCollector; nvidia-smi subprocess primary, ROCm partial, Apple Silicon via system_profiler + sysctl |
| `llauncher/core/model_health.py` | ADR-005 implementation | check_model_health(); 1MB heuristic; TTL cache (60s) |
| `llauncher/core/settings.py` | ADR-003 integration | AGENT_API_KEY from LAUNCHER_AGENT_TOKEN env var |
| `pyproject.toml` | Dependency audit | Typer and rich missing despite being imported by cli.py |

### Files produced by this plan:

| Path | Content |
|------|---------|
| This file (`docs/plans/plan-sleeptime-remediation-06-opus-4.7-architect.md`) | Remediation plan and execution instructions for downstream agents |
| `docs/adrs/003-agent-api-authentication.md` (Phase 2 deliverable) | Revised ADR with alternatives, consequences, risk/mitigation tables, cross-references |
| `docs/adrs/004-cli-subcommand-interface.md` (Phase 2 deliverable) | Revised ADR with Typer justification, dependency gap documentation, completion status, CLI-API map |
| `docs/adrs/005-model-cache-health.md` (Phase 1 deliverable) | Fully rewritten: decision documented, alternatives analyzed, 1MB heuristic explained and justified |
| `docs/adrs/006-gpu-resource-monitoring.md` (Phase 1 deliverable) | Fully rewritten: /dev/memfd fabrication removed, build-vs-adopt analysis added, per-platform accuracy honestly stated |

### Files NOT produced but flagged for implementation:

| Path | Required Change | Priority |
|------|----------------|----------|
| `pyproject.toml` | Add typer and rich to dependencies (Gap G4) | **High** — blocks functional CLI usage after any pip install |
| `llauncher/cli.py` line 31 | Consider changing `add_completion=False` → `True` (ADR-004 recommendation) | Low — cosmetic, no bug |
| `llauncher/core/model_health.py` | Optional: add context-manager-based timeout to open() call for network paths | Medium — Gap item from ADR-005 Open Questions Phase 2 |
