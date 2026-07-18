# Architecture: llauncher — Ground-Physics Conformance

> **Status: DRAFT — awaiting operator ratification (`auto:draft` tier).**
> Generated 2026-06-13 by code audit of `llauncher/` at branch `docs/conformance-audit`.
> Grounds against `design-docs/ecosystem-ground-physics/CODE_CONSTITUTION.md`
> (the six handle-tagged data-plane invariants) and `GROUND_PHYSICS.md`
> (the development-plane disciplines). Shaped by
> `design-docs/agent-constitution/ARCHITECTURE-GOVERNANCE-TEMPLATE.md`
> (five-gate instantiation discipline).

This document declares `llauncher`'s conformance to the ecosystem ground-physics
**data-plane invariants** and **development-plane disciplines**. Its purpose is to
distinguish a valid composition from a violation — not to describe what the code
currently does in general, but to state what it must do per the constitution and to
name the gaps that remain, each grounded in a `path:symbol` citation or marked
absent.

`llauncher` holds a privileged position in the ecosystem physics: **it is the mint**
(`ONE-MINT`). `ModelConfig.name` is the single authority for local-model identity
across every downstream consumer. That makes its conformance load-bearing for the
whole alignment roadmap — Phase 1 (`EMIT-CANONICAL`) is the keystone fix, and it
runs through this codebase.

---

## The invariant (read this first)

Six data-plane rules, plus six development-plane disciplines. All apply
simultaneously.

### Data plane — *identity is conserved*

1. **`ONE-DOOR`.** The core is reachable through exactly one contracted surface, in
   both directions. Dependencies point downward only: endpoints → orchestration →
   core → models; siblings do not import siblings. The mechanism is a **perception
   firewall** — the boundary is enforced by import structure and PR review, not by a
   lint gate or scoped mount, so it stops cross-layer edges from being *authored*; it
   does not structurally prevent a running process from reaching across. *(→ ADR-LLNCH-008;
   `docs/ARCHITECTURE.md`)*

2. **`STATELESS-CORE`.** The core holds no cross-call state; resolve-use-release per
   call. State lives in the `LauncherState` facade above core, rebuilt from disk on
   every refresh. *(→ ADR-LLNCH-008)*

3. **`ONE-MINT`.** `ModelConfig.name` is the single authority that mints canonical
   local-model names. No use site re-declares or re-stringifies a name; envelopes
   (port, log filename, process title) derive *from* the name, never bend it.
   *(→ ADR-LLNCH-010, ADR-LLNCH-016; CLAUDE.md `ONE-MINT`)*

4. **`IDENTITY⊥ENVELOPE`.** Identity is independent of where the model runs and how
   it is reached. `ModelConfig` carries no port (port is a call-site concern). *(→
   ADR-LLNCH-010)*

5. **`EMIT-CANONICAL / PARSE-AT-THE-DOOR`.** The mint stamps the canonical name onto
   the wire untransformed (`--alias = ModelConfig.name` at llama-server start, so
   `/v1/models` reports it byte-for-byte); ingress parsing migrates persisted shapes
   deterministically at the door, once, or fails loud — never dual-parses, never
   trust-and-degrades on an unknown canonical identity. *(→ ADR-LLNCH-016 (emit), ADR-LLNCH-017
   (parse-at-the-door anchor); CLAUDE.md `EMIT-CANONICAL`, `PARSE-AT-THE-DOOR`)*

   5a. **Fail loud on command-arg collisions (NAMED, currently UNENFORCED).**
   `llauncher` must reject or de-duplicate config flags that collide with
   native-emitted llama-server flags; **silent first-wins loss is forbidden.** This
   is a data-plane invariant under `PARSE-AT-THE-DOOR` — a config that *reads* as
   updated while the runtime keeps the old value is identity/parameter drift across
   the command-assembly door. *(→ issue #156 — the anchoring violation; UNDECIDED
   enforcement surface)*

6. **`CONSERVE-ACROSS-THE-DATA-BOUNDARY`.** Identity is preserved when data leaves
   the control plane for the data plane and when products return — carried as a tag
   sourced from the mint, asserted on re-entry. `llauncher` does not itself operate a
   bulk data plane; it supplies the canonical tag (`EMIT-CANONICAL`) that downstream
   data-plane tools (sk-mcp BulkEmbedder, thought-vault) conserve. *(→ ALIGNMENT
   ROADMAP Phase 4 — downstream; UNDECIDED here)*

### Development plane — *discipline is conserved*

7. **Durable emission at decision time.** Decisions land in ADRs, commit messages,
   the structured audit log — not the window. *(→ ADR-LLNCH-008 audit log)*
8. **Signals are first-class.** Friction surfaces as named signals to the caller and
   the durable record. *(→ agent-constitution ADR-0001)*
9. **Honest failure, typed.** BLOCKED / HALTED / FAILED dispositions. *(→
   agent-constitution ADR-0007)*
10. **Grounding before the dependent write; opinion locality; assigned enforcement.**
    *(→ GROUND_PHYSICS Part II)*

---

## The layers

(Per `docs/ARCHITECTURE.md`; dependencies point downward.)

### ENDPOINT (`agent/` HTTP · `mcp_server/` stdio · `ui/` Streamlit · `cli.py`)

**What lives here:** the surfaces that receive caller requests and translate them
into orchestration verbs.

**Rules:**
- Imports orchestration, never the reverse.
- The HTTP `agent/` and the `remote/` client are **peers across the network
  boundary** — they share the wire contract and nothing else; neither imports the
  other in Python.

### ORCHESTRATION (`operations/` stateless verbs · `state.py` `LauncherState` facade)

**What lives here:** `start · stop · swap · delete · orphan · preflight`; the
`LauncherState` facade (ADR-LLNCH-008).

**Rules:**
- Uses core; must not know its callers (no upward import to endpoint layers).
- The facade is the *only* holder of cross-call state; core stays stateless.

### CORE (`core/` config · process · settings · lockfile · audit_log · model_health)

**What lives here:** the contracted core surface.

**Rules:**
- Imports models only; never imports `state`, `operations`, `agent`, `remote`, `ui`,
  `mcp_server` (no upward edge). Callers pass what core needs.

### MODELS (`models/` pydantic data types — the floor)

**What lives here:** `ModelConfig` (the mint), `RunningServer`, `ChangeRules`.

**Rules:**
- Depends on nothing (the floor). `models → anything` is forbidden.

### REMOTE (client) (`remote/` NodeRegistry · RemoteNode · RemoteAggregator)

**Rules:**
- Reaches a node ONLY over HTTP to `agent/` endpoints; does not import `agent`.

---

## What the invariant does NOT govern (out of scope)

- **`CONSERVE-ACROSS-THE-DATA-BOUNDARY` data-plane operation.** `llauncher` mints and
  emits the canonical tag but does **not** run a bulk-ingestion data plane. The
  round-trip assertion lives in the downstream data-plane tools (sk-mcp,
  thought-vault, ALIGNMENT_ROADMAP Phase 4). `llauncher`'s obligation is to *supply*
  a conservable identity, which it discharges via `EMIT-CANONICAL`. This is a scoping
  fact, not an exception.

- **The shared `ModelRef`/`Endpoint` type split (`IDENTITY⊥ENVELOPE` as a published
  type).** `llauncher` satisfies the *substance* of `IDENTITY⊥ENVELOPE` today (port
  is not on `ModelConfig`, ADR-LLNCH-010), but the *shared typed package* that consumers
  import (`ModelRef{name,provider,kind}` + `Endpoint{base_url}`) is ALIGNMENT_ROADMAP
  Phase 2 — not yet published. Out of `llauncher`'s unilateral scope; an
  ecosystem-package decision.

- **EXO governance series (epistemic layer).** *The EXO governance series
  (thought-vault-integration, ADR-EXO-001..005) is a separate, higher governance
  layer — epistemic governance over a corpus-derived value system — that consumes
  llauncher's data-plane identity guarantees. llauncher declares conformance against
  the ground-physics data-plane invariants and the agent-constitution development
  disciplines, which the EXO experiment depends on but does not redefine.*

- **Security/feature opt-in posture is not a backcompat shim.** Default-off agent
  auth (ADR-LLNCH-003 / ADR-LLNCH-017) is security stance, explicitly unaffected by the
  no-shim rule (CLAUDE.md `PARSE-AT-THE-DOOR`).

---

## Diagram

```
   ENDPOINT   agent/ (HTTP) ──┐   mcp_server/   ui/   cli.py
                              │        │         │      │
                              └────────┴────┬────┴──────┘
                                            ▼            ═══ contract boundary ═══
   ORCHESTRATION   operations/  ·  state.py (LauncherState facade, ADR-LLNCH-008)
                                            ▼
   CORE            core/  (config · process · settings · ...)   ← STATELESS
                                            ▼
   MODELS          models/  ModelConfig  ← THE MINT (ONE-MINT)

   REMOTE (client) remote/ ──HTTP──▶ agent/   (peers; no Python import — except the
                                               known regression at registry.py:85)
```

---

## Why the mint must conserve identity

`llauncher` is the single authority (`ONE-MINT`) for every local-model name in the
ecosystem. If the name drifts here, it drifts everywhere downstream: a router that
acquires `"embeddinggemma-300M-F32"` against `llauncher`'s registry cannot match a
server that reports a GGUF filename or a port-derived string on `/v1/models`. Every
re-stringification dialect downstream (`Backend:` prefixes, `.gguf` suffixes,
`loaded_model_on_8081` anchoring) exists *because* the mint historically did not stamp
its own name on the wire. `EMIT-CANONICAL` removes the reason for those dialects to
exist — which is why it is the keystone (ALIGNMENT_ROADMAP Phase 1) and why this
codebase's conformance is leveraged across the ecosystem.

The core is stateless (`STATELESS-CORE`, ADR-LLNCH-008) so that the read tools, the UI, and
the MCP surface all observe the same disk-backed truth on every refresh rather than a
process-start snapshot — the stale-state class of bugs ADR-LLNCH-008 closed.

---

## Current conformance (honest) — Branch B (audited)

The invariant above is the target. The code conforms on most points; the gaps are
named with evidence below.

### Per-invariant conformance table

| Invariant | Verdict | Evidence (`path:symbol`) | Enforcement surface | Implemented gate? | Traced ADR(s) |
|---|---|---|---|---|---|
| `ONE-DOOR` | **Violated** (one known regression) | `llauncher/remote/registry.py:85` — `from llauncher.agent.auth import resolve_agent_token` inside `_resolve_local_token` is a live `remote → agent` import edge (the regression `docs/ARCHITECTURE.md` documents as known) | import-boundary review; **no lint/CI gate** | No — prose-backed (review) | ADR-LLNCH-008; `docs/ARCHITECTURE.md` forbidden edges |
| `STATELESS-CORE` | **Compliant** | `llauncher/state.py:LauncherState.refresh` reloads `ConfigStore.load()` + process table each call; cross-call state lives only in the facade, not in `core/` | same-in/same-out tests (`tests/integration/test_state_integration.py`); core holds no retained instance state | Partial — tests exist; no structural "core has no module state" gate | ADR-LLNCH-008 |
| `ONE-MINT` | **Compliant** | `llauncher/models/config.py:ModelConfig.name` is the sole identity field; envelopes derive from it — `llauncher/core/process.py:log_stem_for` sanitizes for *log filenames only* (injective map, #63/#146), proven not to leak into the wire alias | single typed identity; grep-gate on inline id strings | Partial — no grep-gate in CI; envelope/identity split held by review | ADR-LLNCH-010, ADR-LLNCH-016 |
| `IDENTITY⊥ENVELOPE` | **Compliant (substance); type-split NOT-YET-IMPLEMENTED (shared pkg)** | `llauncher/models/config.py:ModelConfig` carries `name` but **no** `port`/`host` (dropped at the door, `from_dict_unvalidated`, ADR-LLNCH-010); port supplied at call site (`core/process.py:build_command(config, port, host)`) | the type split + type-check | Substance: yes (port absent from type). Shared `ModelRef`/`Endpoint`: no — Phase 2, upstream | ADR-LLNCH-010 |
| `EMIT-CANONICAL` | **Compliant — keystone LANDED** | `llauncher/core/process.py:build_command` line 112 — `cmd.extend(["--alias", config.name])`, byte-for-byte, immediately after `-m`; `--alias` kept in `DENIED_EXTRA_ARG_FLAGS` (`models/config.py:41`) so a config cannot override the minted identity. Landed via **PR #158** (merged). Tests: `tests/unit/test_process.py:TestBuildCommandAlias` | argv assembly + deny-list + adjacency test | **Yes** — unit test asserts exact alias, verbatim unicode/spaces, single occurrence | ADR-LLNCH-016 (canonical self-swap); issue #120 |
| `PARSE-AT-THE-DOOR` (config ingress) | **Compliant** | `llauncher/core/config.py:ConfigStore.load` → `ModelConfig.from_dict_unvalidated` migrates legacy shape **once at the door** (drops `default_port`/`port`/`host`; list→str `extra_args`), no dual-parse. Token sidecar `remote/registry.py:_load_node_tokens` returns `{}` on corrupt — a deliberate security fail-safe on a non-canonical sidecar, **not** a canonical-identity trust-and-degrade | migrate-at-door; fail-loud on unknown canonical shape | Partial — migration is structural; no schema gate rejecting *unknown* config keys (silent-drop posture) | ADR-LLNCH-017 (PARSE-AT-THE-DOOR anchor) |
| `PARSE-AT-THE-DOOR` 5a — **command-arg collision** | **Violated — UNENFORCED (open bug)** | `llauncher/core/process.py:build_command` lines 119–183: native flags (`--ctx-size`/`-c`, `--batch-size`, `--ubatch-size`, `--parallel`, `--threads-batch`, sampling) are emitted, then `extra_args` is appended (line 182–183) — llama-server is first-wins, so a colliding `extra_args` flag is **silently lost**. `DENIED_EXTRA_ARG_FLAGS` only guards `--alias`/`--api-key`/`-m`/`--model`/`--host`/`--port`, not the parameter family | reject-at-config-save + assert-at-command-assembly | **No — NONE** (this is the open bug) | issue #156 (anchoring violation); relates #92, #152 |
| `CONSERVE-ACROSS-THE-DATA-BOUNDARY` | **Compliant for llauncher's role (supply); round-trip NOT-IN-SCOPE here** | `llauncher` supplies the conservable tag via `EMIT-CANONICAL` (above). No bulk data plane in this repo; the re-entry assertion lives downstream (sk-mcp/thought-vault, Phase 4) | round-trip test in the data-plane tool | N/A here — downstream | ALIGNMENT_ROADMAP Phase 4 |
| Dev-plane: durable emission | **Compliant** | `llauncher/core/audit_log.py` (JSONL, commanded-vs-observed); ADR lifecycle `accepted/ → completed/ → superseded/` | structured audit log + ADR convention | Yes (audit log emitted on CRUD, `core/config.py`) | ADR-LLNCH-008 |
| Dev-plane: signals / honest-failure-typed | **Compliant (prose/convention)** | autonomy contract bounce rule (CLAUDE.md); honest-failure typing in agent constitution | review convention | No structural gate | agent-constitution ADR-0001/0007 |

> No row carries an empty Evidence cell. Rows whose code does not exist in this repo
> (shared `ModelRef` package; data-plane round-trip) are marked NOT-YET-IMPLEMENTED /
> NOT-IN-SCOPE with the reason, per template gate 1 (reachability).

---

## What "instant fail" looks like

| Violation | Valid form |
|---|---|
| `remote/registry.py` imports `agent.auth` to read the local token (`remote → agent` edge) | Hoist the token *read* path into `core`; `remote` reads from core, token *materialization* stays in `agent` (`docs/ARCHITECTURE.md` prescribed fix) |
| Core module imports `state`/`operations`/`agent` to fetch a dependency | Invert: the caller (orchestration/endpoint) passes what core needs downward |
| Read tool returns a process-start snapshot of running servers | `LauncherState.refresh()` reloads disk + process table per call (`STATELESS-CORE`) |
| Build an id by appending `.gguf` / prefixing `Backend:` / deriving from port or path | Source the name from `ModelConfig.name`; envelopes (log filename) derive from it via `log_stem_for`, never the reverse |
| `--alias` set from a sanitized/port-derived string, or omitted (llama-server reports GGUF filename) | `cmd.extend(["--alias", config.name])` — byte-for-byte mint name (`build_command`, PR #158) |
| A config supplies `--alias` in `extra_args` to override identity | `DENIED_EXTRA_ARG_FLAGS` rejects it (bare and `=` form) at save and assignment |
| Config ingress dual-parses a legacy and a new shape, or falls back to `"unknown"` on an unrecognized one | Migrate deterministically once at the door (`from_dict_unvalidated`) or fail loud |
| `extra_args` carries `--batch-size 4096`; config reads updated but runtime keeps 512 (silent first-wins loss) | **TARGET (unenforced):** reject the collision at config-save, or expose the field structurally + assert no duplicate at command-assembly — never silently drop (issue #156) |

---

## Relation to the decision record

| ADR / decision | What it fixes | File / status |
|---|---|---|
| ADR-LLNCH-008: Launcher state — stateless facade | `STATELESS-CORE`; `ONE-DOOR` (facade is the state holder above core); durable audit log | `docs/adrs/accepted/adr-llnch-008-launcher-state-stateless-facade.md` — Accepted |
| ADR-LLNCH-010: Port ownership at call site | `IDENTITY⊥ENVELOPE` (port not on `ModelConfig`); `ONE-MINT` (name is identity, port is envelope) | `docs/adrs/completed/adr-llnch-010-port-ownership-at-call-site.md` — Completed |
| ADR-LLNCH-016: Canonical self-swap | `EMIT-CANONICAL` / `ONE-MINT` (canonical name round-trips through swap) | `docs/adrs/completed/adr-llnch-016-canonical-self-swap.md` — Accepted/Completed |
| ADR-LLNCH-017: Trusted-host session-token issuance | `PARSE-AT-THE-DOOR` anchor (migrate-at-door, no dual-parse; cites the no-shim rule) | `docs/adrs/draft/adr-llnch-017-session-token-issuance.md` — Draft |
| ADR-LLNCH-003: Agent API authentication | Security opt-in posture (out-of-scope-as-shim) | `docs/adrs/completed/adr-llnch-003-agent-api-authentication.md` — Completed |
| ADR-LLNCH-015: Orphan policy | `STATELESS-CORE` reconciliation (per-refresh dedupe) | `docs/adrs/accepted/adr-llnch-015-orphan-policy.md` — Accepted |
| Issue #120 / PR #158 | `EMIT-CANONICAL` keystone — `--alias = ModelConfig.name` | merged (PR #158) |
| Issue #63 / #146 | `ONE-MINT` envelope defect — injective log-filename map (fixed in envelope space) | closed |
| Issue #156 | `PARSE-AT-THE-DOOR` 5a — command-arg collision silent first-wins loss | **OPEN** — anchoring violation, no gate |
| `docs/ARCHITECTURE.md` | `ONE-DOOR` forbidden edges; names the `remote → agent` regression | tracked-in-repo |

---

## Phase status vs ALIGNMENT_ROADMAP

| Phase | Outcome | Installs | llauncher status (this audit) |
|---|---|---|---|
| **0** | Doctrine written; contested ADR amended | (all, as law) | Done — doctrine lives in `design-docs/`; referenced by `CLAUDE.md` |
| **1** | The mint stamps the wire | `EMIT-CANONICAL` | **LANDED.** `build_command` emits `--alias config.name` (`core/process.py:112`, PR #158). The keystone is in place; the parking note in `CLAUDE.md`/roadmap ("parked in `DENIED_EXTRA_ARG_FLAGS` pending #87/#10") is **stale** — #87/#10 closed, `--alias` is now *emitted* by the launcher and the deny-list entry is retained deliberately (guards the mint from a config override, not a parking) |
| **2** | Shared identity type published | `ONE-MINT`, `IDENTITY⊥ENVELOPE` | **Blocked upstream.** Shared `ModelRef`/`Endpoint` package not yet published; `ModelConfig` satisfies the substance but the importable type does not exist. Out of llauncher's unilateral scope |
| **3** | Backprop into consumers | `EMIT/PARSE` across consumers | **Blocked on Phase 2.** Consumer re-stringification removal (LIP/sk-mcp/LAS/thought-vault) is downstream; cannot prove canonical round-trip until the type lands |
| **4** | Data plane conserves identity | `CONSERVE` | **Downstream.** Depends on Phase 1 (done) + Phase 3d; the corpus tag round-trip lives in sk-mcp/thought-vault, not here |

**Net:** llauncher has discharged its keystone obligation (Phase 1). The remaining
ecosystem phases (2–4) are upstream/ecosystem-blocked on the shared package and on
backprop into consumers — not on llauncher source.

---

## Known limit (honest)

Per `CODE_CONSTITUTION.md` §Use: the enforcement surfaces named above are *named*, not
all *implemented*. The `ONE-DOOR` import boundary, the `ONE-MINT` grep-gate, and the
#156 collision check are **prose-/review-backed and therefore fragile** — treat as
provisional, and prefer adding the gate over trusting the prose. The `EMIT-CANONICAL`
keystone is the strongest invariant here precisely because it carries a structural
unit-test gate; the `remote → agent` edge is the weakest because nothing structural
stops it being re-authored.
