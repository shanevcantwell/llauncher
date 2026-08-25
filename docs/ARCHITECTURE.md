# Architecture: llauncher

This document defines the **layering-and-mint** invariant of `llauncher` and makes the
boundary between layers enforceable. Its purpose is to distinguish a valid composition
from a violation — not to describe what the code currently does, but to state what it
must do and to name the gaps that remain.

> This document is the single home for the layer map: the terse edit-time diagram and
> forbidden-edge table live in the section immediately below, and the invariant-first
> governing version with the audited conformance ledger follows it. There is no separate
> edit-time file — one home, so there is nothing to disagree with.

---

## Edit-time layer map (read this first while editing)

> Minimal distillation of the layering doctrine for use *at edit time*. Deeper context:
> the full invariant below, the ADRs it cites (esp. ADR-LLNCH-008 stateless facade, ADR-LLNCH-010
> port-keyed endpoints), and the historical `docs/1-architecture-layers.md` /
> `docs/2-cross-layer-reach.md`.

```
ENDPOINT        agent/ (HTTP)   mcp_server/ (stdio)   ui/ (Streamlit)   cli.py
                     │                 │                   │             │
                     └─────────────────┴────────┬──────────┴─────────────┘
                                                ▼
ORCHESTRATION   operations/  (stateless verbs: start · stop · swap · delete · orphan · preflight)
                state.py     (LauncherState facade — ADR-LLNCH-008)
                                                ▼
CORE            core/  (config · process · settings · lockfile · audit_log · model_health)
                                                ▼
MODELS          models/  (pydantic data types — the floor)

REMOTE (client) remote/  (NodeRegistry · RemoteNode · RemoteAggregator)
                used by ui/ and cli;  reaches a node ONLY over HTTP to agent/ endpoints.
```

**The one rule: dependencies point downward. Siblings do not import siblings.**

- Endpoints orchestrate; orchestration uses core; core uses models.
- `remote` and `agent` are **peers across the network boundary** — they share the HTTP
  wire contract and nothing else. `remote` is a client; `agent` is a server. Neither
  imports the other in Python.
- If two siblings need the same helper, **hoist it down** into a shared lower layer
  (`core`), don't reach sideways.

### Forbidden edges (guard these)

| Edge | Why it's wrong | Do instead |
|---|---|---|
| `remote → agent` | client importing its server | call over HTTP, or hoist the shared helper into `core` |
| `core → {state, operations, agent, remote, ui, mcp_server}` | core must not import upward | invert: caller passes what core needs |
| `models → anything` | data types are the floor | keep them dependency-free |
| `state`/`operations` → endpoint layers | orchestration must not know its callers | endpoints depend on orchestration, never the reverse |

> Resolved: `remote/registry.py` previously imported `agent.auth.resolve_agent_token` (a
> `remote → agent` edge) to source the local token. Fixed by hoisting the token *read*
> path into `core.agent_token` and keeping token *materialization* in `agent.auth`
> (#171, landed via #202/SP-1). Audited conforming in the conformance ledger below.

### Enforced UI boundary (ADR-LLNCH-025)

`ui/` reaches the backend **only** through `state`/`operations`/`remote` — backend verbs
via the orchestration facades, all node I/O via `remote/` (`NodeRegistry` / `RemoteNode`
/ `RemoteAggregator`). A UI module must never:

- do its own HTTP to a node (`httpx` / `requests` / `urllib` / `http.client` / `socket` /
  `aiohttp`) — node I/O is `remote/`'s job; or
- import a peer/sibling endpoint (`llauncher.agent.*`, `llauncher.mcp_server.*`,
  `llauncher.cli`).

This is **enforced statically** by `tests/architecture/test_ui_layer_boundaries.py`: an
AST scan over `llauncher/ui/**` that fails fast on either breach, citing this file and
the offending `file:line`. It is the deterministic catch for the cross-layer reach (a UI
tab hitting a node URL directly) that previously escaped to an alpha tag. A behavioral
complement (`tests/ui/` AppTest harness, `forbid_direct_http`) asserts the same at
runtime for the tabs it drives.

**The UI is a thin client app.** It holds no authoritative state. Everything it shows is a
render of ground truth fetched this pass, via `state`/`operations`/`remote`; everything it
does is a request to the backend.

`st.session_state` is for **view state only** — widget continuity, an in-flight
confirmation the user hasn't answered yet. It never carries port occupancy, model
identity, or server lifecycle truth. When the UI and the backend disagree, the UI is
wrong, and the repair is a re-fetch — never a wider `session_state`.

Enforcement is honestly split: the *reach* half (which modules the UI may touch) is
mechanical — the AST guard above. The *state* half (what `session_state` may carry) is
prose today; if prose fails to hold, the named follow-up is a lintable `session_state`
key convention (#410).

---

## The full invariant (the governing version)

Seven rules. All seven apply simultaneously. Where this section and the edit-time map
above disagree, this one is the target.

1. **Dependencies point downward.** Each layer imports only from layers beneath it;
   siblings never import siblings. The mechanism is a **perception** firewall — the
   import graph. A module physically cannot name a symbol it does not import, so the
   rule stops a cross-layer edge from being *authored*. It does **not** bound what a
   running process can reach over other channels (HTTP, a shell, a `sys.path` insert) —
   only what the source may import. *(→ the edit-time layer map above; ADR-LLNCH-008)*

2. **`remote` and `agent` are network peers, not Python neighbors.** They share exactly
   one thing: the HTTP wire contract. `remote` is a client; `agent` is a server. Neither
   imports the other in Python — a shared helper is hoisted **down** into `core`, never
   reached sideways. *(→ ADR-LLNCH-009)*

3. **The orchestration facade is stateless.** `LauncherState` and `operations/` hold no
   cross-call session state; every read rebuilds the live process table from the
   filesystem and the OS per call. Identity of result depends on current ground truth,
   not call history. *(→ ADR-LLNCH-008)*

4. **`ModelConfig.name` is the mint (ONE-MINT).** It is the single authority for
   local-model identity across the ecosystem. Every other string — port, adapter, log
   filename, process title, wire alias — is an **envelope** derived from the name, never
   a redefinition of it. An envelope defect is fixed in envelope space; the name does not
   bend to absorb it. *(→ ecosystem ground-physics constitution: ONE-MINT /
   IDENTITY⊥ENVELOPE; issues #146/#63)*

5. **The wire emits the canonical name (EMIT-CANONICAL).** llama-server starts with
   `--alias = ModelConfig.name` so `GET /v1/models` reports the minted identity
   byte-for-byte — no transformation, no sanitization. The flag is launcher-owned: it is
   on the `extra_args` deny-list so no config can override the minted identity.
   *(→ ecosystem ground-physics constitution: EMIT-CANONICAL; issue #120)*

6. **Parse at the door; no backcompat shims (PARSE-AT-THE-DOOR).** A persisted artifact
   is parsed once into its validated shape, or the load fails loud. Never dual-parse two
   shapes of the same artifact; never trust-and-degrade on an unrecognized one. When a
   shape changes, migrate deterministically in place, once. *(→ ecosystem
   ground-physics constitution: PARSE-AT-THE-DOOR; project `CLAUDE.md` local rule;
   ADR-LLNCH-017)*

7. **Ports are owned at the call site.** `start` / `stop` / `swap` and every port-keyed
   endpoint take the port as an explicit caller-supplied argument; the orchestration
   layer never allocates or derives it. `ModelConfig` carries no port. *(→ ADR-LLNCH-010;
   ADR-LLNCH-011)*

---

## The layers

Top (consumer) to bottom (substrate). The one rule across all of them: **dependencies
point downward; siblings do not import siblings.**

### Endpoints (the doors)

**What lives here:** `agent/` (HTTP server), `mcp_server/` (stdio JSON-RPC), `ui/`
(Streamlit), `cli.py`.

**Rules:**
- Endpoints **orchestrate** — they call orchestration verbs, they do not reimplement
  them.
- Siblings do not import siblings: `ui` does not import `mcp_server`, `cli` does not
  import `agent`. A shared need is hoisted into `core`.
- `agent/` is the only server-side door the network client (`remote`) may reach, and
  only over HTTP.
- **`ui/` reaches the backend only through `state`/`operations`/`remote`** — backend
  verbs via the orchestration facades, all node I/O via `remote/` (`NodeRegistry` /
  `RemoteNode` / `RemoteAggregator`). A UI module must never do its own HTTP to a node
  (`httpx`/`requests`/`urllib3`/`urllib.request`/`http.client`/`socket`/`aiohttp`/`pycurl`
  — the guard's `_HTTP_*` sets are authoritative) nor import a peer endpoint
  (`agent`/`mcp_server`/`cli`). **Enforced statically** by
  `tests/architecture/test_ui_layer_boundaries.py` (ADR-LLNCH-025) — the deterministic catch
  for the cross-layer reach that escaped to an alpha tag. The UI is a **thin client**: no
  authoritative state; `st.session_state` is view-state only (see the enforced UI
  boundary above).

### Orchestration (stateless verbs + facade)

**What lives here:** `operations/` (start · stop · swap · delete · orphan · preflight),
`state.py` (`LauncherState` — ADR-LLNCH-008).

**Rules:**
- Holds no cross-call session state; rebuilds from ground truth per call (rule 3).
- Does not know its callers — never imports an endpoint layer.
- Ports arrive as explicit arguments (rule 7).

### Core (mechanics)

**What lives here:** `core/` — `config`, `process`, `settings`, `lockfile`,
`audit_log`, `model_health`, `gpu`, `marker`.

**Rules:**
- Imports only `models/` and the `util/` substrate. Never imports upward (`state`,
  `operations`, `agent`, `remote`, `ui`, `mcp_server`).
- Owns the mint→envelope derivations: the `--alias` emission (`process.build_command`)
  and log-filename sanitization (`process.log_stem_for`) derive from `ModelConfig.name`
  (rules 4–5).

### Models (the floor)

**What lives here:** `models/` — Pydantic data types, including `ModelConfig`.

**Rules:**
- Dependency-free relative to llauncher's own layers — imports nothing from `core` or
  above.
- `ModelConfig.name` is the mint (rule 4); the type carries no port (rule 7).

### remote (network client peer)

**What lives here:** `remote/` — `NodeRegistry`, `RemoteNode`, `RemoteAggregator`.

**Rules:**
- Reaches other nodes **only** over HTTP to `agent/` endpoints. Imports nothing from
  `agent` in Python (rule 2).
- Used by `ui/` and `cli`; it is a client, structurally symmetric to `agent` across the
  wire.

---

## What the invariant does NOT govern (out of scope)

- **The managed `llama-server` processes themselves.** llauncher owns their
  start/stop/swap/status lifecycle, but their internals — inference telemetry, slot
  state, KV-cache pressure — are out of process and outside this invariant. Reading them
  (e.g. monitoring-from-llama-server work) is a **new consumer of `core`**, not a
  layering exception: it enters through `core` like any other mechanic and surfaces
  through `agent/` + MCP like any other read. This is a scoping fact, not a license to
  bypass.
- **The `util/` substrate** (`llauncher/util/`, e.g. the TTL cache). It sits **beneath**
  `core` and is used across layers. `core` importing `util` is a downward edge, not a
  violation; `util` imports nothing internal upward.
- **External tooling shelled out by `core`** — `nvidia-smi` (GPU metrics), the
  `llama-server` binary. These are process dependencies, not Python layers; the
  layering rule does not reach across the process boundary.

---

## Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ ENDPOINTS (the doors)                                         │
│   agent/ (HTTP)   mcp_server/ (stdio)   ui/ (Streamlit)  cli  │
└───────┬──────────────────┬──────────────────┬───────────┬────┘
        │                  │                  │           │
        └──────────────────┴────────┬─────────┴───────────┘
                                     ▼
┌─────────────────────────────────────────────────────────────┐
│ ORCHESTRATION (stateless)                                     │
│   operations/  (start·stop·swap·delete·orphan·preflight)      │
│   state.py     (LauncherState facade — ADR-LLNCH-008)               │
└─────────────────────────────┬───────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ CORE (mechanics)                                              │
│   config · process · settings · lockfile · audit_log          │
│   model_health · gpu · marker                                 │
└─────────────────────────────┬───────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ MODELS (the floor)   — Pydantic types; ModelConfig (the mint) │
└─────────────────────────────────────────────────────────────┘
                              ▲
                  ┌───────────┴───────────┐
                  │  util/  (substrate)    │  used across layers; imports nothing up
                  └────────────────────────┘

   remote/ ──── HTTP wire contract ────▶ agent/        (peers; no Python edge)
   (NodeRegistry, RemoteNode, RemoteAggregator)         used by ui/ + cli

   core/ ──shell──▶ nvidia-smi, llama-server binary     (out of process; out of scope)
```

Notes on the diagram:
- Every endpoint arrow crosses down through orchestration; none reaches into `core` or
  `models` sideways past it.
- `remote` and `agent` touch only across the **HTTP wire contract** line — no Python
  import edge runs between them.
- `util/` sits beside-and-beneath the stack; a downward import into it is not a
  violation.
- The shelled-out external processes sit outside the boundary entirely.

---

## Why downward-only layering and a single mint

**Downward-only layering** keeps each layer substitutable and testable in isolation. If
`core` could import `operations` or an endpoint, the floor would depend on the building:
a unit test of a `core` mechanic would drag in the orchestration facade and its
filesystem assumptions, and a change to an endpoint could silently alter `core`
behavior. The peer split between `remote` and `agent` is the same principle across the
network boundary — a client that imported its server would couple the two into one
deployable, defeating the symmetric hub-spoke topology (ADR-LLNCH-009) where any node is both.

**A single mint** is what keeps model identity from fragmenting into per-consumer
dialects. The ecosystem has many envelopes for the same model — a port, a log filename,
a process title, a wire alias, a router key. If any of them were allowed to *be* the
identity rather than *derive from* it, two envelopes could disagree and there would be no
authority to adjudicate. `ModelConfig.name` is that authority; `EMIT-CANONICAL` is the
keystone that pushes the authority onto the wire so downstream routers match against it
rather than re-deriving their own string. This is why an envelope defect (e.g. a
log-filename sanitization collision, #146/#63) is fixed in envelope space — bending the
name to dodge the collision would make the mint serve the envelope instead of the
reverse.

---

## Current conformance (honest)

The invariant above is the target. The code is **audited** below; it conforms on six
rules and is in violation on one.

### Conforming (audited)

| Rule | Evidence (`path:symbol`) | Note |
|------|--------------------------|------|
| 2 — `remote`/`agent` as network peers | `llauncher/remote/registry.py:144` — `from llauncher.core.agent_token import resolve_agent_token` inside `_resolve_local_token` | Previously imported from `agent.auth` (a `remote → agent` edge, #171). Fixed by hoisting the token *read* path into `core.agent_token`; `agent.auth` keeps token *materialization* and re-exports the surface. Landed via #202 (SP-1). |
| 3 — stateless facade | `llauncher/state.py:LauncherState.refresh` | Rebuilds process table + config from disk per call; read-side MCP tools call `state.refresh()` per request (`mcp_server/tools/servers.py:server_status`, `:get_server_logs`). Only persistent fields are within-process orphan-dedup sets. |
| 4 — ONE-MINT | `llauncher/models/config.py:ModelConfig.name` | Sole identity field; envelopes derive from it (`core/process.py:log_stem_for`, `:log_path_for`, the `--alias` emission). |
| 5 — EMIT-CANONICAL | `llauncher/core/process.py:build_command` (`cmd.extend(["--alias", config.name])`) + `llauncher/core/process.py:DENIED_EXTRA_ARG_FLAGS` (`--alias` denied) | Canonical name emitted unconditionally; launcher-owned via deny-list. The deny-list moved out of `models/config.py` (a pydantic field validator) into `core/process.py` — the single launch-time enforcement point — with ADR-026 / issue #477. **See reconciliation note below.** |
| 6 — PARSE-AT-THE-DOOR | `llauncher/core/config.py:ConfigStore.load` (single parse); `llauncher/remote/registry.py:_load_node_tokens` (type-filters, does not dual-parse a legacy bare-string shape); `llauncher/core/audit_log.py:read_entries` (single enum coercion, corrupt lines skipped) | No dual-parse / trust-and-degrade observed. |
| 7 — port at call site | `llauncher/operations/start.py:start(model_name, port, …)`, `operations/stop.py:stop(port, …)`, `operations/swap.py:swap(model_name, port, …)`; MCP schemas require `port` (`mcp_server/tools/servers.py`) | Port is always caller-supplied; `ModelConfig` carries none. |

### Violations

| Violation | Why it breaks the invariant | Evidence (`path:symbol`) | Resolved by |
|-----------|----------------------------|--------------------------|-------------|
| **Models imports Core (upward edge).** `ModelConfig`'s blacklisted-ports default-factory sources its list from `core.settings`. | Breaks rule 1: `models/` is the floor and must be dependency-free relative to llauncher's own layers; importing `core` inverts the arrow. | `llauncher/models/config.py:20` — `from llauncher.core.settings import BLACKLISTED_PORTS as _ENV_BLACKLISTED_PORTS` (consumed at `ChangeRules.blacklisted_ports`, line 469) | Invert the dependency: pass the blacklist in at construction/validation time, or hoist the constant to `models`/`util` so the edge points down. Tracked: #170. |

> **Reconciliation note (rule 5).** `EMIT-CANONICAL` conforms as of
> `core/process.py:build_command`. However the project `CLAUDE.md` and the ecosystem
> alignment roadmap (Phase 1) still describe it as *pending* / *the single
> highest-leverage fix*. Those references are **stale**; the implementation landed (issue
> #120). Reconciling them — closing #120 if open, advancing the roadmap phase, and
> trimming the CLAUDE.md "until this lands" framing — is tracked as a separate doc task.
> This conformance row is the audited ground truth.

---

## What "instant fail" looks like

One row per invariant rule. Each violation paired with the contracted correct shape.

| Violation | Valid form |
|-----------|------------|
| A `core/` module imports `from llauncher.operations import …` or `from llauncher.agent import …`. | `core/` depends only on `models/` and `util/`; whatever it needs from above is passed in by the caller. |
| `models/config.py` imports a constant from `core.settings`. | The constant lives in `models`/`util` (downward), or is injected at construction — the floor imports nothing from above. |
| `remote/registry.py` imports a function from `agent.auth`. | The shared read path is hoisted into `core`; `remote` reaches `agent` only over the HTTP wire contract. |
| `LauncherState` caches a server list at construction and serves it on later calls. | Every read calls `refresh()` and rebuilds from the live process table + disk. |
| A second module redefines model identity (e.g. keys a registry by sanitized log name instead of `ModelConfig.name`). | All identity flows from `ModelConfig.name`; the sanitized log name is an envelope derived from it, never an alternate authority. |
| llama-server is started without `--alias`, so `/v1/models` reports the GGUF filename; or `--alias` is moved into `extra_args` where a config can override it. | `build_command` emits `--alias config.name` unconditionally; `--alias` stays on `core/process.py:DENIED_EXTRA_ARG_FLAGS`, raising `DeniedExtraArgError` at launch. |
| A loader accepts both a legacy bare-string and a new dict shape of `node_tokens.json`, branching on which it sees. | The artifact is migrated to one shape at the door (or the load fails loud); exactly one shape is parsed thereafter. |
| `operations.start` allocates a free port itself when the caller omits one. | `port` is a required argument; the caller (endpoint) chooses it per ADR-LLNCH-010. |
| A UI tab keeps lifecycle truth in `st.session_state` (port occupancy, running-model identity) and renders from it instead of re-fetching. | `session_state` holds only view state; every render rebuilds lifecycle facts via `state`/`operations`/`remote`. |

---

## Relation to the decision record

| ADR / decision | What it fixes | File / status |
|----------------|--------------|---------------|
| ADR-LLNCH-008: LauncherState stateless facade | Rules 1, 3 — the downward layer arrow and the stateless orchestration facade | `docs/adrs/accepted/adr-llnch-008-launcher-state-stateless-facade.md` — Accepted |
| ADR-LLNCH-009: Symmetric hub-spoke topology | Rule 2 — `remote`/`agent` as network peers sharing only the HTTP wire | `docs/adrs/superseded/adr-llnch-009-symmetric-hub-spoke-topology.md` — Superseded by ADR-LLNCH-018 |
| ADR-LLNCH-010: Port ownership at call site | Rule 7 — port is a call-site argument; `ModelConfig` carries none | `docs/adrs/completed/adr-llnch-010-port-ownership-at-call-site.md` — Completed |
| ADR-LLNCH-011: Swap semantics v2 | Rule 7 — the port-keyed `swap` and its preflight | `docs/adrs/completed/adr-llnch-011-swap-semantics-v2.md` — Completed |
| ADR-LLNCH-016: Canonical self-swap | Rules 4–5 — canonical identity preserved across an agent's self-swap | `docs/adrs/completed/adr-llnch-016-canonical-self-swap.md` — Completed |
| ADR-LLNCH-017: (node-token persistence) | Rule 6 — parse-at-the-door; the bare-string `node_tokens.json` dual-parse caught in review is the anchor violation | `docs/adrs/` — see CLAUDE.md local rule |
| Issue #120: EMIT-CANONICAL `--alias` | Rule 5 — the wire reports the minted name | Implemented (`core/process.py`); roadmap reference stale, see reconciliation note |
| Ecosystem ground-physics constitution | Rules 4, 5, 6 — ONE-MINT, IDENTITY⊥ENVELOPE, EMIT-CANONICAL, PARSE-AT-THE-DOOR | `operating-doctrine` → `ground-physics/GROUND_PHYSICS.md` (six data-plane invariants + dev-plane disciplines; migrated 2026-06-22 from the design-docs scratchpad, then to `operating-doctrine`). The earlier "out-of-tree (private) / restated in `CLAUDE.md`" note was stale — no such restatement existed. |
