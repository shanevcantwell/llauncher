# Brief: ADR Restructure (Planner)

**Source Review:** plan-sleeptime-remediation-00-review-opus-4.7-complete-review.md, architect agent output  
**Scope Limitation:** Only modify files under `/home/node/github/llauncher/docs/adrs/`. No production code or test changes.

---

## Objective

Rewrite ADRs 003, 004, 005, and 006 so they meet genuine architectural documentation standards: real decisions between alternatives with honest consequences, proper cross-references, no cargo-cult templating. Merge ADR-005 (Model Cache Health) and ADR-006 (GPU Resource Monitoring) into a single cohesive "Pre-flight Validation Pipeline" ADR.

---

## Arch Agent Verdict Summary

| ADR | Original Verdict | Core Problem |
|-----|------------------|-------------|
| 003 (Auth) | SHALLOW | Only documents one approach; critical questions (rotation, audit, binding) deferred to "Phase 2" but should be decided now or explicitly rejected. Open Questions section does load-bearing work that the Decision should own. |
| 004 (CLI) | ADEQUATE | Most honest of the four. Named tradeoffs ("double-discovery problem"). Weak on alternatives considered and missing table-stakes features (shell completion, exit codes, machine-readable output discussion). |
| 005 (Model Cache Health) | RUBBER-STAMP | Reads as a feature spec in ADR clothing. No choice between alternatives documented. "Decision" is "add a check and an endpoint." Conflates three distinct features (pre-flight check, health endpoint, register CLI command). |
| 006 (GPU Monitoring) | SHALLOW with scope-overlap | Build vs. adopt decision not evaluated. Apple MPS approach via `/dev/memfd` is fabricated (doesn't exist on macOS; memfd is Linux-only). Per-process VRAM attribution imprecision not surfaced to operator. Overlaps substantially with 005. |

**Cross-cutting:** None of the four ADRs reference each other despite obvious coupling (003's auth gates 005/006 endpoints, 004's CLI consumes all three). Set reads as four parallel feature tickets retrofitted into ADR format.

---

## Output Documents Required

### Document 1: Rewritten ADR-003 — Agent API Authentication (merge from old 003)

**New scope:** The decision isn't just "X-Api-Key header middleware" — it's the complete auth model for the agent HTTP API. Must include:

**Context:** Why does this system need authentication? What threat model applies (internal network, CLI tools, peer agents)? Who are the actors?

**Options Considered:**
1. **X-Api-Token (bearer token) via header** — chosen approach
2. mTLS — rejected: too complex for internal agent-to-agent comms; requires PKI
3. OAuth/JWT — rejected: overkill for static key exchange between known agents
4. Unix socket binding + OS-level permissions — rejected: limits multi-host deployments
5. SSH-tunnel-only access — rejected: adds operational complexity, not compatible with CLI design

**Decision:** X-Api-Key header via `LAUNCHER_AGENT_TOKEN` environment variable. All non-exempt paths require authentication. Exempt paths: `/health`, `/docs`, `/redoc`. When token is unset, server starts without auth but emits a startup WARNING at every bind address (not just 0.0.0.0). OpenAPI schema suppressed (`openapi_url=None`) when auth is active to prevent route enumeration even with correct credentials.

**Consequences:**
- *Positive:* Simple for CLI tools and peer agents to implement; no PKI required; works across hosts in same network
- *Negative (acknowledged):* Key must be manually distributed to all agents; key rotation requires coordinated restart of all nodes using the token; no per-agent key scoping or audit trail in Phase 1
- *Mitigation for known negatives:* ADR references future work item for Phase 2: periodic key rotation via config reload (no restart), optional per-node keys with NodeRegistry-scoped tokens, request logging with anonymized auth checks

**Auth Path Table:** (Updated to match implementation)
| Path | Auth Required? | Reason |
|------|---------------|--------|
| `/health` | No | Liveness probe must work even during auth config mistakes |
| `/docs`, `/redoc` | No (but suppressed when auth active) | Developer convenience; suppressed in production to prevent schema leakage |
| `/_/openapi.json` | No (suppressed when auth active — see server.py fix) | Internal FastAPI route |
| All other paths | Yes | Agent API — peer agents and CLI tools must authenticate |

**Cross-references:**
- ADR-004: CLI commands (`llauncher node add`, `llauncher model start`) use `--api-key` flag to set headers
- ADR-005/006 (merged): Health/status endpoints are exempt from auth — this is intentional; these endpoints return capability info, not control-plane operations

**Open Questions:** Explicitly documented and scoped:
- Key rotation mechanism design (deferred, with timeline estimate)
- Per-agent key scoping for multi-team deployments (deferred, no commitment)
- Audit logging of authentication failures (deferred, justified by operational cost analysis)

---

### Document 2: Rewritten ADR-004 — CLI Subcommand Interface (from old 004)

**This is the best existing draft. Focus on tightening:**

**Context:** Operators need a unified interface for local state management and remote node control. Current per-tool invocation pattern (`python -m llauncher.server`, `curl` for health checks, manual JSON editing) is error-prone.

**Options Considered:**
1. **Typer-based CLI with subcommand groups** — chosen approach (Typer already a dependency via FastAPI ecosystem; rich table rendering built-in; excellent type inference from function signatures → automatic help text and validation)
2. Click framework — rejected: different paradigm than existing FastAPI stack; no automatic Pydantic integration for argument parsing
3. argparse subcommands — rejected: verbose boilerplate, no automatic table rendering, manual help text generation
4. Plain `python -m` invocations with shell aliases — rejected: no type validation, no discoverability via `--help`, not CI/CD friendly

**Decision:** Typer app at `llauncher/cli.py` with four subcommand groups: `model`, `server`, `node`, `config`. Entry point registered in `pyproject.toml` as console script. Rich table output by default; `--json` flag for machine-readable consumption. Single executable entry point (`llauncher`) replacing multiple invocation patterns.

**Consequences:**
- *Positive:* Type-safe argument parsing (Typer validates types automatically); consistent UX across all operations; CI/CD friendly with `--json` output; no shell alias management
- *Negative (acknowledged):* "Double-discovery problem" — CLI must discover both local and remote state, which creates cognitive complexity when the same subcommand has different behaviors locally vs. remotely documented explicitly in help text per variant)
- *Mitigation:* Help text explicitly distinguishes local-state commands from peer-node commands; `--remote <node-id>` flag overrides default behavior

**Missing features to acknowledge (design decisions):**
- Exit codes: Not currently implemented — all successful operations return 0, errors return 1. Decision: defer exit-code granularity to Phase 2 when usage data shows which failure modes operators need in shell pipelines.
- Shell completion: Not implemented. Typer supports `typer-cli` for this but adds an extra dependency. Deferred until user demand justifies it.

**Cross-references:**
- ADR-003: CLI commands that interact with authenticated nodes use `--api-key <key>` or read from config
- ADR-005/006 (merged): `llauncher model health` command wraps the `/models/health` endpoint; `llauncher server start-with-eviction` triggers VRAM pre-flight

---

### Document 3: Merged ADR-??? — Pre-flight Validation Pipeline (combines old 005 + 006)

**This is the biggest change. Two previously separate concerns unified under one architectural decision.**

**Title:** ADR-XXX: Pre-flight Validation Pipeline

**Context:** Before launching a model server, we need to verify two things: (1) the model file is valid and loadable, and (2) sufficient GPU VRAM is available for the requested configuration. Currently these checks are siloed: model health is a filesystem check with no GPU awareness; GPU monitoring provides metrics but no launch gate. Together they form a coherent pre-flight pipeline that prevents two common failure modes: OOM launches (no VRAM) and corrupted-model loads (bad GGUF files).

**Options Considered:**
1. **Integrated pipeline with shared validation contract** — chosen approach
2. Separate independent checks — rejected: creates inconsistency when both checks report "ok" but their combined result would fail; each check independently extended `/status` without coordination
3. Single endpoint that orchestrates sub-checks via callback — over-engineered for current scope (only two check types); could be reconsidered if plugin architecture grows

**Decision:** A unified pre-flight pipeline with:
- **Check registry pattern**: Each health check registers itself and returns a typed result (`ModelHealthResult`, `GPUHealthResult`)
- `/status` endpoint merges all check results under one response envelope
- `/models/health/{model_name}` for per-model diagnostic detail (not just global)
- `/start-with-eviction` endpoint runs VRAM pre-flight as a gate before allowing the launch; returns 409 with specific required vs. available numbers when insufficient
- Model health check uses "safe-to-load" heuristic: file exists + readable + size > 1 MiB (explicitly documented as conservative — will pass some invalid files, but won't fail on any valid file)
- GPU backends: NVIDIA (nvidia-smi JSON), AMD ROCm (rocm-smi table parse), Apple MPS (system_profiler Metal parse with explicit single-GPU limitation documented)

**VRAM Estimation Contract:**
- `_estimate_vram_mb(model_path)` uses filename pattern matching for base size estimation
- Supports common model sizes: 3B (~2GB), 7B (~4GB), 13B/14B (~8GB), 30B/34B (~20GB), 70B (~40GB)
- Falls back to minimum estimate for unrecognized filenames (conservative — overestimates rather than underestimates)
- Partial offload (`n_gpu_layers < 999`) scales VRAM proportionally
- **Limitation acknowledged:** Per-process VRAM attribution on shared GPUs is imprecise. The 409 gate is an estimate, not a guarantee. Operators should err on the side of setting aside more VRAM than estimated.

**GPU Backend Detection Strategy:**
1. Try NVIDIA first (most common enterprise GPU): `shutil.which("nvidia-smi")` → subprocess call → JSON parse
2. Try ROCm second: `shutil.which("rocm-smi")` → subprocess call → table regex parse  
3. Try MPS third (macOS only): `system_profiler SPDisplaysDataType` → Metal GPU detection (explicitly limited to single-GPU Apple Silicon)

**Known Limitations:**
- Backend tool availability does not guarantee GPU hardware is functional (e.g., nvidia-smi present but driver crashed) — logged and surfaced via degraded flag on `/status`
- ROCm table parse format may change between rocm-smi versions — no stability contract guaranteed by AMD
- MPS detection via `system_profiler` has no formal output spec — Apple may change it without notice
- Memory fragmentation within a GPU is not accounted for; the estimate assumes contiguous allocation

**Consequences:**
- *Positive:* Single endpoint for pre-flight results; prevents both OOM and corrupted-model launches; degraded-mode awareness (`/status.gpu.degraded` flag) tells operators when checks couldn't complete
- *Negative (acknowledged):* Adding subprocess calls to health checking increases startup latency; each backend has different failure modes that must be handled gracefully

**Cross-references:**
- ADR-003: Health and status endpoints are explicitly exempt from authentication — necessary for cluster orchestration tools to probe node state without credentials
- ADR-004: CLI `llauncher model health` and `llauncher server start-with-eviction` commands interface with this pipeline

---

### Document 4: New ADR (if numbering requires it) — Future Work Boundaries

**Or add a "Future Considerations" section to the merged pre-flight ADR documenting what is explicitly out of scope:**
- Real-time GPU monitoring (periodic polling beyond pre-flight check)
- Multi-node VRAM-aware scheduling (distributing models across nodes based on available VRAM)
- GPU memory leak detection over time
- Model versioning / GGUF header validation beyond file-size heuristic

---

## Formatting Requirements

Each ADR must follow the standard template:
```markdown
# ADR-NNN: Title

Date: YYYY-MM-DD

## Status
Proposed | Accepted | Superseded by [ADR-NNN]

## Context
(What problem are we solving? What forces are at play?)

## Decision
(What did we decide? Be specific.)

## Alternatives Considered
(List 2+ real options that were considered and why they were rejected)

## Consequences
(Honest tradeoffs — what this enables AND what it makes harder)

## Cross-references
(other ADRs this connects to, explicitly with file paths)

## Open Questions / Future Work
(Explicitly scoped deferred decisions — NOT used as a crutch for incomplete analysis)
```

**Cross-reference requirements:** Each new ADR MUST have at minimum two cross-references. No orphaned ADRs are acceptable.

---

## Acceptance Criteria

1. All four original ADR topics are addressed (either in rewritten individual ADRs or merged form)
2. Every ADR has ≥2 Alternatives Considered with honest rejection reasoning
3. Every ADR has a Consequences section with both positive and negative tradeoffs explicitly stated
4. The merged pre-flight ADR correctly replaces two separate concerns under one coherent architecture
5. Cross-references create a connected graph — no ADRs stand alone
6. No fabricated technical claims (the `/dev/memfd` fabrication from old ADR-006 must be removed)
7. Auth path table in 003 matches actual implementation
8. "Open Questions" are scoped as deferred items with explicit justification, not used to punt core decisions
