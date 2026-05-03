# v2 Orientation Spike — Gap Analysis

**Date:** 2026-05-02
**Subject:** Live tree at `/home/shane/github/shanevcantwell/llauncher/` vs. ratified ADRs 008 / 009 / 010 / 011
**Author:** orientation-spike (read-only walk)

> Caveat on the "Qwen evaluation" sub-axis: the spike was asked to compare the
> code against a reverse-engineered `docs/PRODUCT_REQUIREMENTS.md`. **That file
> does not exist in the live tree.** No PRD is present at any path under
> `docs/`. The PRD-accuracy section therefore evaluates the next-best
> proxies — the four `docs/1..4-*.md` architecture briefs (which read like the
> source material the ADRs cite as "PRD §1.2", "§3.1", "§4.2", "§11.3", etc.)
> plus the historical artifacts in `docs/PLAN-*.md` and the review folder.
> Treat the Qwen-eval section as evaluating *those* documents.

---

## 1. Live Tree Structure

```
llauncher/
├── llauncher/                          # main package
│   ├── __init__.py                     # version
│   ├── __main__.py                     # python -m llauncher entrypoint
│   ├── cli.py                          # Typer CLI (model/server/node/config groups)
│   ├── state.py                        # LauncherState dataclass + EvictionResult
│   ├── agent/                          # HTTP Agent (FastAPI)
│   │   ├── __init__.py / __main__.py
│   │   ├── server.py                   # uvicorn entrypoint, --stop, create_app
│   │   ├── routing.py                  # all routes; module-level _state singleton
│   │   ├── middleware.py               # X-Api-Key auth (ADR-003)
│   │   └── config.py                   # AgentConfig env-var loader
│   ├── mcp_server/                     # stdio MCP server
│   │   ├── server.py                   # dispatcher + lazy _mcp_state singleton
│   │   └── tools/
│   │       ├── models.py               # list_models, get_model_config
│   │       ├── servers.py              # start_server, stop_server, swap_server,
│   │       │                           #   server_status, get_server_logs
│   │       └── config.py               # add/update/remove/validate
│   ├── core/                           # infra layer
│   │   ├── config.py                   # ConfigStore (atomic write via .tmp)
│   │   ├── process.py                  # psutil scans, build_command,
│   │   │                               #   start_server, stop_server_by_*,
│   │   │                               #   wait_for_server_ready, find_available_port
│   │   ├── settings.py                 # env-backed module constants
│   │   ├── model_health.py             # check_model_health + 60s TTL cache (ADR-005)
│   │   └── gpu.py                      # GPUHealthCollector + 5s TTL cache (ADR-006)
│   ├── models/config.py                # Pydantic: ModelConfig, RunningServer,
│   │                                   #   AuditEntry, ChangeRules
│   ├── remote/                         # multi-node
│   │   ├── node.py                     # RemoteNode (httpx client wrapper)
│   │   ├── registry.py                 # NodeRegistry persisting nodes.json
│   │   └── state.py                    # RemoteAggregator (cross-node ops)
│   ├── ui/                             # Streamlit
│   │   ├── app.py                      # entrypoint, sidebar, tab routing
│   │   ├── utils.py
│   │   └── tabs/                       # dashboard, model_card, forms,
│   │                                   #   manager, model_registry, nodes, running
│   └── util/cache.py                   # _TTLCache helper
├── pi-footer-extension/                # TypeScript footer (Pi harness, ADR-001)
│   ├── footer-budget.ts                # ~700 LOC; reads ~/.llauncher/nodes.json,
│   │                                   #   queries /status, formats footer
│   └── test-footer-validation.mjs
├── tests/                              # pytest (unit, integration, ui)
├── scripts/{run.sh,run.bat}
├── docs/
│   ├── adrs/                           # 001..006, 008..011 (no 007 in tree)
│   ├── 1-architecture-layers.md        # current layer brief (PRD-ish)
│   ├── 2-cross-layer-reach.md
│   ├── 3-refresh-reconcile-patterns.md
│   ├── 4-state-ownership.md
│   ├── PLAN-architectural-remediation.md
│   ├── PLAN-phase1-implementation.md
│   ├── MCP.md
│   ├── plans/                          # CLI-IMPLEMENTATION, phase1-verification
│   ├── reviews/                        # this file lives here
│   ├── scratchpad/                     # drafts, dashboard UX, refactor notes
│   └── generated/TEST_SUITE_SUMMARY.md
├── pyproject.toml                      # version 0.2.0a0
└── pytest.ini, README.md, htmlcov/, agent.{log,pid}, agent_startup.log
```

Three console-script entry points are declared in `pyproject.toml`:

- `llauncher` → `llauncher.cli:app`
- `llauncher-mcp` → `llauncher.mcp_server.server:main`
- `llauncher-agent` → `llauncher.agent:main`

---

## 2. Per-ADR Gap Analysis

### ADR-008 — LauncherState as Stateless Facade

#### Already aligned

- `ConfigStore.save()` is atomic via `.tmp` rename
  (`llauncher/core/config.py:51-55`). ADR-008's "atomic write on mutation"
  bullet is satisfied.
- `check_model_health` already pushes a TTL cache (60 s) into the source layer
  (`llauncher/core/model_health.py:46`), and `GPUHealthCollector` does the same
  with 5 s (`llauncher/core/gpu.py:75-77`). ADR-008's "caching pushed down"
  guidance is already true for these two sources.
- The MCP read tools `list_models`, `get_model_config`, `server_status`, and
  `get_server_logs` all call `state.refresh()` on the dispatch-provided state
  before reading
  (`llauncher/mcp_server/tools/models.py:53,92`,
  `llauncher/mcp_server/tools/servers.py:150,173`).
  The "MCP read tools never refresh" symptom called out in ADR-008 §Context #1
  has *already been fixed* — the ADR's framing is stale on that point.
- `LAUNCHER_AGENT_NODE_NAME` env var with `socket.gethostname()` fallback
  (`llauncher/agent/routing.py:25-29`) is already the v2 identity contract.

#### Conflicts

- `LauncherState` is still a `@dataclass` with `models`, `running`, `audit`,
  and `rules` fields and a `refresh()` method
  (`llauncher/state.py:48-74`). Every element ADR-008 says to remove is still
  there.
- `__post_init__` still calls `self.refresh()` (`state.py:64-66`), so every
  construction triggers a full process-table scan + config reload. ADR-008
  requires construction to be cheap.
- Three module-level singletons remain:
  - `agent/routing.py:13` — `_state: LauncherState | None = None`
  - `mcp_server/server.py:17` — `_mcp_state: "LauncherState | None" = None`
  - `ui/app.py:22-24` — `st.session_state["state"]`
  Plus the temp-instance pattern in `ui/tabs/model_card.py:293` (`temp_state =
  LauncherState()`) and three per-invocation constructions in
  `cli.py:154,168,181`. This is exactly the "four LauncherState instances"
  pattern §Context calls out — and it is real (see also §4).
- `record_action()` appends to an in-memory list at `state.py:610`. There is
  no JSONL file, no env-configurable path, no append-on-disk. `audit.jsonl`,
  `LAUNCHER_AUDIT_PATH`, and the commanded-vs-observed distinction do not
  exist.
- `state.refresh()` reloads `self.models` (`state.py:71`) but does **not**
  reset `self.audit` — the §Context "audit log is reset on refresh" claim is
  not true in the current code. (Notable: ADR-008 reasons partly from a
  defect that does not exist; see §4.)

#### Absent

- No lockfile mechanism. No `~/.llauncher/run/`, no `{port}.lock` file format,
  no writer in `process.start_server`. `LAUNCHER_RUN_DIR` is never read.
- No argv sentinel. `build_command` (`core/process.py:61-156`) does not pass
  `--alias` or any equivalent marker. Process identity is inferred purely from
  `"llama-server" in proc.name() / cmdline` and an exact `model_path` string
  match (`state.py:112-121`).
- No reconciliation table behavior at all — there is nothing for stale
  lockfiles to be reconciled against.
- No "commanded vs. observed" audit verbs. The action vocabulary in audit
  records is `start | stop | evict | rollback | update`, all commanded; no
  `observed_stopped`, `observed_orphan`, or `swap_aborted`.
- No persistence path or rotation/retention story for the audit log.
- `record_action` field set is `(timestamp, action, model, caller, result,
  message)` (`models/config.py:135-144`) — missing the `port`, `from_model`,
  `to_model` shape ADR-008/011 implies the audit needs.

---

### ADR-009 — Symmetric Hub/Spoke Topology

#### Already aligned

- The same package ships agent + MCP + UI + CLI; one binary, one mental model.
  The topology is *factually* symmetric today.
- `nodes.json` is per-node (`remote/registry.py:11`,
  `~/.llauncher/nodes.json`). Each node manages its own list. Adding/removing
  a node never propagates. The §Decision "nodes.json is each node's local peer
  list" matches reality.
- `LAUNCHER_AGENT_NODE_NAME` env with `socket.gethostname()` default
  (`agent/routing.py:25-29`) is already the v2 identity rule.
- `pi-footer-extension/footer-budget.ts` reads `~/.llauncher/nodes.json` and
  walks it client-side rather than relying on a central directory — the
  symmetric model is already cooked into the only external consumer.
- Config sovereignty is de facto strict: `ConfigStore` only reads/writes the
  local `~/.llauncher/config.json`. No code path mutates a peer's config.

#### Conflicts

- The remote dispatch path is **single-tier**, not symmetric. `RemoteNode`
  (`remote/node.py:192-244`) only exposes `start_server`, `stop_server`,
  `get_logs` — there is no remote `swap` and no remote config CRUD. A "hub"
  caller cannot CRUD configs on a remote node, so the §Decision
  *"CRUD on configs always targets a specific node"* shape (`target: NodeName
  | None`) cannot actually target a peer for config CRUD even though the
  ADR states it must.
- The Streamlit UI hard-codes the special-case node name `"local"` for
  enabling local features (e.g. `model_card.py:81,200,217,254,286`).
  ADR-009's identity model wants the comparison to be against
  `LAUNCHER_AGENT_NODE_NAME` / `gethostname()`, not the literal string
  `"local"`. The string `"local"` is also auto-injected by
  `NodeRegistry.is_local_agent_ready` and `start_local_agent`
  (`remote/registry.py:170-245`), tightly coupling that name to the local
  loopback.
- No dedicated "tool layer" between endpoints and infra. Each endpoint reaches
  directly into `LauncherState` methods, so there is no shared signature
  carrying `target: NodeName | None`. The short-circuit-vs-remote dispatch
  decision currently lives inline in UI helpers and aggregator methods rather
  than at a single tool-layer entry.

#### Absent

- No `target` parameter anywhere. Tool functions take `model_name`, `port`,
  `caller` — never a node target. The §Decision tool-layer signature is
  unimplemented.
- No self-loop short-circuit. The UI `_handle_start` branches on `node_name ==
  "local"` and skips HTTP — but it does so by calling `state.start_server`
  directly (which works) rather than through a uniform tool function. The CLI
  always operates locally; it has no remote-dispatch path at all (cli.py
  `server start/stop` constructs a `LauncherState()` and operates locally
  unconditionally — no `--node` flag).
- The UI auto-starts the local agent and then sometimes calls
  `state.start_server` directly without going through HTTP — that's the
  "short-circuit" intent but it isn't symmetric with how a remote target
  would be reached, because no shared signature exists.

---

### ADR-010 — Port Ownership at the Call Site

#### Already aligned

- `RunningServer` is keyed by port in the `running` dict
  (`state.py:60`, `agent/routing.py:166-167`). Port is already the runtime
  primary key.
- `core/process.start_server(config, port, ...)` takes port as an explicit
  argument; `build_command` injects `--port {port}` from the runtime
  parameter, not from the config field (`core/process.py:61-93`).
- `wait_for_server_ready(port, ...)` and `is_port_in_use(port)` are both
  port-keyed.
- `ChangeRules.validate_start(config, caller, port)` already accepts an
  explicit port (`models/config.py:164`).

#### Conflicts

- `ModelConfig.default_port` still exists as a field
  (`models/config.py:19-24`) and is used as a fallback throughout:
  - `state.py:143` — `check_port = port or config.default_port`
  - `state.py:216` — `preferred = port or config.default_port`
  - `agent/routing.py:401` — `target_port = port if port is not None else
    config.default_port`
  - `state.py:637` — returned in `get_model_status`
  - `agent/routing.py:214` — surfaced in `/models` response
  - `mcp_server/tools/models.py:67,114` — surfaced in MCP responses
  The whole "fallback chain" the ADR says to delete is still wired in.
- `find_available_port` (`core/process.py:23-58`) implements the auto-allocate
  fallback that ADR-010 §Decision says is removed at the API layer.
  `state.start_server` calls it unconditionally (`state.py:217`).
- `POST /start/{model_name}` exists (`agent/routing.py:278`) — model-keyed,
  no port in the path. ADR-010 explicitly removes this.
- `POST /start-with-eviction/{model_name}` exists
  (`agent/routing.py:374`) — model-keyed with port as optional query param.
  ADR-010 removes this in favor of `POST /swap/{port}`.
- MCP `start_server` tool only takes `model_name`
  (`mcp_server/tools/servers.py:14-25`); MCP `swap_server` takes port + model
  but description still describes "stops any server on the port and starts the
  new model" without distinguishing the swap-precondition (port occupied)
  required by ADR-010 + ADR-011.
- CLI `server start <name> --port` defaults port to `None` and lets state
  auto-allocate (`cli.py:147-159`). ADR-010 wants the CLI to error if neither
  `--port` nor `DEFAULT_PORT` is set, never auto-allocate at the API layer.

#### Absent

- No `POST /start/{port}` endpoint with `{model: str}` body. No `POST
  /swap/{port}` either. The ADR-010 verb table is not in the router.
- No `MCP swap_server(port, model)` shape with the ADR-011 response envelope
  (`{success, action, port_state, ...}`); existing one returns
  `{success, port_state, error, rolled_back, ...}` — close, but no `action`
  field and no enumerated action values.
- No `action`-bearing response envelope anywhere. Every endpoint returns its
  own ad-hoc shape.
- CLI `llaunch` rename is absent. CLI is still `llauncher`. ADR-010 §CLI uses
  `llaunch server swap <port> <model>` as the new shape; no `swap`
  subcommand exists in `cli.py` at all (only `start` and `stop`).

---

### ADR-011 — Swap Semantics v2

#### Already aligned

- A single 5-phase swap mechanic exists in `state._start_with_eviction_impl`
  (`state.py:288-560`). Phase ordering matches ADR-011: pre-flight → stop old
  → start new → readiness → success/rollback. Pre-flight covers model exists
  in config, model file health (ADR-005), port-range validation, and
  same-model-on-other-port check.
- Rollback uses the persisted config of the previous model (`state.py:424`,
  `state.py:474`, `state.py:516`). It calls the same launch + readiness
  mechanic for the rollback path.
- Three of the four ADR-011 callers route to `_start_with_eviction_impl`:
  - MCP `swap_server` — `mcp_server/tools/servers.py:225`
  - HTTP `start-with-eviction` — `agent/routing.py:441`
  - UI eviction-confirm dialog — `ui/tabs/model_card.py:144` (via
    `start_with_eviction_compat`)
  The legacy three-implementation drift ADR-002 fought is gone.
- `EvictionResult.port_state` already encodes the required state literals:
  `"unchanged" | "restored" | "serving" | "unavailable"`
  (`state.py:39`).
- Same-model swap does the right thing in the trivial sense: pre-flight
  doesn't reject because the new-model checks all pass, but currently the
  swap will then *stop and restart* the same model rather than no-op.

#### Conflicts

- `strict_rollback` parameter still exists and is differentiated by caller
  (`state.py:295`). The MCP tool passes `strict_rollback=True`
  (`mcp_server/tools/servers.py:230`); the HTTP agent passes `False`
  (`agent/routing.py:442`); the UI compat wrapper passes `False`
  (`state.py:577`). ADR-011 §Decision §"Caller Differences (Eliminated)"
  removes this parameter.
- Same-model swap is **not** an idempotent no-op. It still stops the old
  process and starts a fresh one (no early-return guard). ADR-011's
  `already_running` action with `port_state=serving` and "no teardown, no
  relaunch" is unimplemented.
- Response shape uses `EvictionResult` fields (`success, port_state, error,
  rolled_back, restored_model, previous_model, new_model_attempted,
  startup_logs`) — close to but not equal to the ADR-011 envelope. Missing:
  `action` enum (`swapped | already_running | rolled_back | failed |
  rejected_preflight | rejected_stop_failed | rejected_in_progress |
  rejected_empty`), `pid`, and `model`.
- The class is still named `EvictionResult`; ADR-011 §Supersession asks for
  `SwapResult` and removal of the `start_with_eviction_compat` tuple wrapper
  (it explicitly calls the compat wrapper "unnecessary; v2 is a clean
  rewrite"). Both still exist (`state.py:30-46`, `state.py:562-583`).
- HTTP `/start-with-eviction/{model_name}` is the wrong shape entirely; ADR
  wants `POST /swap/{port}` body `{model}`. The model is in the path; the
  port is a query param.
- ADR-011 swap precondition is **port occupied**. The current
  `_start_with_eviction_impl` happily proceeds when the port is empty
  (`state.py:390` only branches if `port in self.running`); there is no
  `rejected_empty` outcome. So the verb-precondition contract from ADR-010
  doesn't hold here.

#### Absent

- No in-flight marker file. No `{LAUNCHER_RUN_DIR}/{port}.swap`. No
  `O_EXCL` atomic creation. No `rejected_in_progress` action. Two concurrent
  swaps on the same port will race — the second will see the port empty
  briefly (after the first stops the old model) and proceed.
- No stale-marker reconciliation (because no marker).
- No VRAM headroom pre-flight inside `_start_with_eviction_impl` itself —
  `_check_vram_sufficient` runs only at the HTTP route layer
  (`agent/routing.py:407`), not at the tool/state layer. MCP and UI bypass
  it. ADR-011 §Phase 1 lists VRAM headroom as a pre-flight check at the
  unified layer.
- No `swap_aborted` audit verb. No `port_dead` audit entry on `unavailable`
  outcome.
- No harness self-swap contract documentation surfaced to callers — the
  response shape doesn't carry a "session reset" hint distinguishing
  reconnect-to-new vs. reconnect-to-restored. The `port_state` value alone
  encodes that, but there's no LLM-readable `action` to consume.

---

## 3. Out-of-PRD Findings

Things in the live code that the PRD-proxy documents (`docs/1..4-*.md`)
underplay or omit entirely:

- **Authentication middleware** (`llauncher/agent/middleware.py`): full
  `X-Api-Key` / `hmac.compare_digest` machinery with exempt-path frozenset
  for `/health`, `/docs`, `/redoc`, `/openapi.json`. ADR-003 covers this but
  the architecture briefs do not mention auth at all. `AGENT_API_KEY` is
  read from `LAUNCHER_AGENT_TOKEN` (`core/settings.py:57`), and `RemoteNode`
  threads `X-Api-Key` headers through every call (`remote/node.py:88-97`).
- **Pi footer extension subtree** (`pi-footer-extension/`): a 700-line
  TypeScript module that is the largest external consumer of llauncher's
  `/status` endpoint. ADR-001 mentions it; the architecture briefs do not.
  This is the entity ADR-008 names as "the highest-frequency consumer" of
  the harness footer contract — its existence is load-bearing on Tier 2.
- **Local-agent autostart from the UI** (`remote/registry.py:200-245`,
  `ui/app.py:113-152`): the Streamlit app, on first load, will spawn
  `llauncher-agent` as a detached subprocess and inject a `"local"` node
  into `nodes.json`. This is a substantial side-effect that doesn't appear
  in the layer briefs. The auto-injected `"local"` name fights the
  symmetric-topology decision in ADR-009.
- **`RemoteAggregator` offline cache** (`remote/state.py:54-67`): when a node
  goes offline, the aggregator returns *cached* server lists with `[OFFLINE]`
  appended to `config_name`. This is a real behavior consumers see; the
  briefs don't describe it.
- **`logs_path` is dead** (`models/config.py:117`,
  `agent/routing.py:165`): the field exists on `RunningServer` and is
  surfaced in `/status` responses, but nothing populates it — `process.py`
  writes logs to `~/.llauncher/logs/{name}-{port}.log` (`process.py:192`)
  but never stores that path on the server record.
- **Free-form `extra_args` string with `shlex.split`**
  (`models/config.py:48`, `core/process.py:153-154`): runs arbitrary
  llama-server flags from a single string field. This is a real surface
  area — and a security smell on multi-user hosts (see §6) — that the
  briefs gloss.
- **`refresh_running_servers` mutates the entire `running` dict atomically**
  (`state.py:110`) — i.e., `state.running` does not preserve the structured
  metadata (start_time) we may have set during `start_server`; a refresh
  resets it to `datetime.now()` (state.py:104). This is a known bug-shaped
  behavior that the briefs do not flag.
- **`stop_server` uses `find_server_by_port` with port-flag heuristics**
  (`core/process.py:258-287`) — including handling for `--port=N` and `-pN`
  forms. This is broader than the cmdline parser in `state.py:91`, which
  only handles `--port N`. The two parsers can disagree.
- **CLI `node` and `config` subcommands** (`cli.py:215-348`): full
  registry CRUD and config validation/path display in the CLI. Architecture
  briefs only mention server start/stop subcommands.
- **`ChangeRules.blacklisted_ports` defaults to `{8080}`**
  (`models/config.py:161`) — a hardcoded default that overlaps with
  `DEFAULT_PORT=8080` in `core/settings.py:41`. This is a foot-gun: a user
  who runs an llauncher under defaults will be unable to start any server
  because the default port is also blacklisted. Briefs don't flag this.

---

## 4. PRD Accuracy Observations

Evaluating `docs/1..4-*.md` (the architecture briefs the ADRs treat as PRD)
plus references in the ADRs to "PRODUCT_REQUIREMENTS.md §X.Y" sections that
do not exist as a single file in the tree.

### Where it's accurate

- The high-level layer diagram in `docs/1-architecture-layers.md` is faithful:
  three endpoints, central `LauncherState`, infra in `core/`, remote in
  `remote/`. The class methods enumerated under "Layer 2: State
  Orchestration" line up with `state.py` — `refresh`,
  `refresh_running_servers`, `_find_model_by_path`, `start_server`,
  `stop_server`, `_start_with_eviction_impl`, `get_model_status`, `can_start`,
  `can_stop`. The signatures match.
- `docs/1-architecture-layers.md` correctly catches the
  "creates its own temporary `LauncherState()` for port collision checks"
  pattern in `model_card.py` (line 72) — confirmed at
  `ui/tabs/model_card.py:293`.
- The MCP "no refresh" claim *was* historically true and is *now* corrected
  in code (`mcp_server/tools/models.py:53,92` and
  `mcp_server/tools/servers.py:150,173`), but `docs/1-architecture-layers.md`
  still describes it as `**No refresh.**` (line 61). The brief is *behind*
  the code. ADR-008 §Context #1 inherited that staleness.
- The four-instance count in §Context of ADR-008 is *real*: agent
  (`agent/routing.py:13`), MCP (`mcp_server/server.py:17`), UI session-state
  (`ui/app.py:22-24`), CLI per-call (`cli.py:154,168,181`), plus the temp
  instance in `ui/tabs/model_card.py:293`. So actually *five* construction
  sites, not four — the brief slightly under-counts.

### Where it diverges from the code

- **MCP refresh discipline.** `docs/1-architecture-layers.md` lines 60-61
  say `list_models`, `get_model_config`, `swap_server`, `server_status`,
  `get_server_logs` "**No refresh.**" — false today. They all call
  `state.refresh()` (the read tools) or `_start_with_eviction_impl` which
  internally `refresh_running_servers()` at end (the swap tool). ADR-008
  cited this as a live problem; it's stale.
- **Audit reset on refresh.** ADR-008 §Context #3 cites a TODO `# Reset
  audit on full refresh? (specify behavior)` and claims `refresh()` resets
  the audit list. Inspection of `state.py:68-74`: `refresh()` reloads
  `self.models` and re-scans processes, but **does not touch
  `self.audit`**. The brief and the ADR both reason from a defect that
  isn't in the code. (The actual problem is that the audit log is
  never persisted, regardless of refresh.)
- **`find_all_llama_servers` filter.** The brief implies a strict
  `"llama-server"` substring match; actual code (`core/process.py:304`)
  matches against either `proc.name()` or any cmdline element containing
  `"llama-server"`. This is broader and easier to false-positive on
  unrelated processes (e.g., a shell that happens to have
  `"llama-server"` in an argument), which is exactly why ADR-008's argv
  sentinel matters.
- **CLI shape in ADR-010 §CLI** (`llaunch server swap`): no `swap` CLI
  subcommand exists. Only `start` and `stop`. The ADR describes the
  desired shape, not the current shape; that's fine for a forward-looking
  ADR, but if the same brief was used to derive the ADR's *Context*
  paragraph it would have been wrong about today's surface.
- **`default_port` is "preferred port".** `docs/4-state-ownership.md`
  / Pydantic field treats `default_port` as a "preferred port" (auto-
  allocates if missing). ADR-010 supersedes this and the field is to be
  removed. Code matches the brief, not the ADR.

### Over-specified vs. under-specified

- **Over-specified:**
  - The brief enumerates eight MCP tools by name with one-line summaries
    that are essentially redoing the docstrings; this duplicates source of
    truth. The MCP tool surface is small enough that the source is the
    PRD.
  - `docs/3-refresh-reconcile-patterns.md` is patterns-as-prose; it
    describes what was a fix-cycle deliberation but reads like a
    requirement document. It locks in implementation details (e.g.
    "single refresh per dispatch") that ADR-008 effectively obviates.
- **Under-specified:**
  - **Audit log.** The briefs and the ADR both say "action logging for
    governance and debugging" but neither pins the *fields* the audit
    must carry. `AuditEntry` lacks `port`, `from_model`, `to_model`,
    `pid` — fields obviously needed for the swap audit story.
  - **External contract for the harness footer.** The pi-footer-extension
    consumes `/status` and walks `nodes.json`, but no document specifies
    the response shape, polling cadence, or stability guarantees. Tier 2
    item — see §5.
  - **Process identity outside of `model_path` exact-match.** The briefs
    don't address what happens when two configs share a `model_path` (e.g.,
    same weights, different sampling presets) — `_find_model_by_path` returns
    the *first* match (`state.py:117-121`).
  - **Logs filename collisions.** `process.py:192` uses
    `{sanitized_name}-{port}.log`; if a config name maps to the same
    sanitized name as another (e.g., `mistral 7b` and `mistral_7b`), they
    collide on disk. Not specified anywhere.
  - **Local agent self-registration.** `is_local_agent_ready` and
    `start_local_agent` write `"local"` into `nodes.json`
    (`remote/registry.py:170-245`). This is a substantive surface that
    should be either documented or removed.

### Anti-pattern smells the PRD treated as decisions

- **"Four LauncherState instances" framing.** ADR-008's §Context #1 reads
  the four instances as a known design that needs to be reframed. Reading
  the code, this is plainly **not** a designed-in invariant — it's
  accidental. Each consumer wrote its own caching to work around state
  being expensive to construct, and nobody owned the cross-cutting
  question. ADR-008's decision (stateless facade) is correct; calling it
  "the four-instance problem" sells the diagnosis short — it's a
  symptom of "no shared service layer," not a counted-cardinality bug.
- **`_start_with_eviction_impl` rollback duplication.** The function has
  three distinct rollback implementations (start exception, readiness
  timeout, readiness exception — `state.py:423-549`). They are
  copy-pasted with minor variations. The PRD-proxy doesn't flag this
  duplication as a smell; ADR-011's "rewrite, not migration" framing
  effectively skips over it.
- **`strict_rollback` boolean-arg flag.** Different callers pick different
  values for the same operation; the brief / ADR-002 institutionalized
  this as MCP-vs-UI strictness. ADR-011 correctly removes it.
- **The `"local"` node string.** Repeatedly used as both an identity and a
  short-circuit signal in UI helpers (`model_card.py:81,200,217,254,286`).
  The brief doesn't flag this conflation; ADR-009's identity-resolution
  decision will need to disentangle it.

---

## 5. Tier 2 Hooks

For each deferred Tier 2 item, what the spike found that informs it.

### Footer contract (REST shape + cadence)

- The consumer is `pi-footer-extension/footer-budget.ts`. It reads
  `~/.llauncher/nodes.json` directly (file path hard-coded
  at `footer-budget.ts:30`) and queries `GET /status` per node it cares
  about. Cadence is not in the file we sampled, but it's a per-token
  footer redraw — high frequency.
- Server side, `/status` returns:
  `{node, running_servers: [...], total_running, gpu}`
  where each running server has `pid, port, config_name, start_time,
  uptime_seconds, logs_path, model_config`
  (`agent/routing.py:157-187`). `model_config` is a full
  `ModelConfig.to_dict()` — large payload. The footer cares mostly about
  `ctx_size` and `parallel`; the rest is wasted bytes.
- `/status` calls `state.refresh_running_servers()` on every request —
  full process-table scan per call. At footer cadence this is O(processes)
  * O(footer_redraws) which is non-trivial.
- ADR-008 names the footer as the highest-frequency consumer and pushes
  reconciliation per request. A Tier 2 footer-contract ADR should consider
  pinning a slimmer endpoint (e.g., `/footer-context/{port}`) or response
  shape (`{ctx_size, parallel, model}` only) to avoid wire-amplifying
  on every keystroke.
- `nodes.json` divergence between footer and llauncher (when a node was
  removed via CLI but footer hasn't picked it up) is silently absorbed by
  the connect-fails-loudly model.

### Logs lifecycle (rotation, retention)

- Logs written at `~/.llauncher/logs/{sanitized_name}-{port}.log` opened
  in `"w"` mode (`core/process.py:197`) — i.e., **truncated on every
  start**. So historical logs are lost across restarts. There is no
  rotation, no size cap, no retention policy.
- `LOG_DIR.mkdir(parents=True, exist_ok=True)` at `process.py:188` — that's
  the entire lifecycle.
- `_tail_file` reads the whole file into memory (`process.py:359`). On a
  long-running server a multi-GB log file could OOM the agent.
- No `LAUNCHER_LOG_DIR` env override. No `LOG_LEVEL`-driven sizing.
- Tier 2 ADR will need to decide: rotate-on-size? rotate-on-restart and
  archive? cap log file size? `streamline_logs` should switch to a
  bounded tail (seek from end).

### Cancellation of in-flight start/swap

- No cancellation primitives. `_start_with_eviction_impl` runs
  synchronously inside a request; `wait_for_server_ready` blocks for up
  to 120 seconds with `time.sleep(check_interval)` (`process.py:391-422`).
- The HTTP agent runs on uvicorn; the FastAPI handler is async-declared
  but calls into blocking psutil/sleep code. A client disconnect does not
  interrupt the swap.
- No `asyncio.CancelledError` handling, no `Task.cancel()`-style design,
  no signal handlers in `_start_with_eviction_impl`.
- Tier 2: cancellation almost certainly needs the in-flight marker
  (ADR-011) as the cancel signal — write `cancel: true` into the marker
  and have the readiness loop check for it, or replace the synchronous
  loop with a structured-concurrency primitive.

### Orphan policy (process matches argv but no lockfile)

- Today there is no lockfile, so every running `llama-server` not in
  `state.running` is technically an "orphan" but is treated as
  fully-managed by `refresh_running_servers` if its `-m` arg matches a
  config. There is no distinction between *llauncher started this* and
  *something else started this*.
- `find_all_llama_servers` (`core/process.py:290-310`) will pick up any
  process containing `"llama-server"` in its name or cmdline — including
  ones started by hand outside llauncher.
- Once the lockfile + argv sentinel land, the orphan-detection rule is
  cheap: lockfile-absent + argv-match → orphan. The default policy ADR-008
  proposes ("leave alone, audit-log `observed_orphan`") is sane;
  empirically the spike found no code path that *would* mistakenly try to
  manage such a process today (no auto-stop sweep), so the do-nothing
  default has zero migration risk.

### Canonical self-swap worked example

- Today's mechanism: an LLM emits `swap_server(port=P, model_name=B)` via
  MCP. Dispatcher invokes `swap_server` in `mcp_server/tools/servers.py:225`,
  which calls `state._start_with_eviction_impl(...)` synchronously. The
  MCP transport delivers the response *after* the new model is up (or
  rolled back).
- The harness's *inference* transport to model A on port P dies during
  Phase 3 (stop) — but the harness's *MCP* transport is independent
  (stdio, separate process). So ADR-011's "stable transport" claim is
  already structurally true; it just isn't *documented*.
- What's missing for a worked example:
  - A round-trip log capturing the harness's MCP request, the in-flight
    response, and the harness's reconnection of the inference channel.
  - Concrete timings (cold-start B vs. hot-cache A — VRAM swap pressure).
  - The `port_state` → harness-action mapping table (the ADR table is
    correct but not yet operationalized in code, since `port_state` is
    not propagated through the MCP `swap_server` response in the
    fully-typed envelope).

---

## 6. Out-of-Scope Findings (Appendix)

Two items the spike noticed that fit the "egregious" bar even though they're
outside the v2 architecture scope.

- **`extra_args: str` is `shlex.split`'d into argv on every start**
  (`models/config.py:48`, `core/process.py:153-154`). Anyone with write
  access to `~/.llauncher/config.json` can inject arbitrary llama-server
  flags — and llama-server flags include `--api-key`, `--lora`, and
  filesystem-path options. This is by-design for a single-user hobby tool,
  but the agent HTTP API exposes config CRUD via MCP (`add_model`,
  `update_model_config`) over the network if the agent is running with
  no `LAUNCHER_AGENT_TOKEN`. With the auth-default-off footgun
  (`agent/server.py:166-181` warns but does not refuse), an unauthenticated
  agent on `0.0.0.0` is a remote arbitrary-flag-injection vector for
  llama-server. ADR-003 mitigates this, but the warning-only default
  bears flagging.

- **Default port collides with default blacklist.** `DEFAULT_PORT=8080`
  (`core/settings.py:41`) and `ChangeRules.blacklisted_ports={8080}`
  (`models/config.py:161`) are both module-defaults. A fresh user with
  no config will hit "Port 8080 is blacklisted" on every start. Not a
  data-loss bug, but a defaulted-into-broken-state bug. Worth a one-line
  fix.

---

## Summary

The live tree implements ADR-008/009/010/011 about **30%** structurally:
endpoints exist, swap mechanic exists with rollback, port is largely the
runtime primary key, multi-node is symmetric in shape, and identity
resolution by env+hostname is in place. The remaining 70% is the v2
delta: `LauncherState`-as-data → facade-as-service, `default_port` →
removed, lockfile + argv sentinel + audit JSONL → new, `/start/{port}`
+ `/swap/{port}` + `action` envelope → new endpoint shape, in-flight
marker → new concurrency primitive, `strict_rollback` flag → removed,
`EvictionResult` → `SwapResult`, the `"local"` node string → properly
identity-derived, and a tool layer carrying `target` → new module.

The PRD-proxy documents (`docs/1..4-*.md`) are accurate as a snapshot of
*one prior moment* in the code's evolution and stale on at least two
points (MCP refresh discipline, audit reset on refresh) where the code
moved on without the docs following. The ADRs, drafted by reading those
proxies, inherited those staleness points — not catastrophically, but
visibly. The "four LauncherState instances" framing in particular is a
real symptom but mis-named as a designed cardinality; the actual disease
is the absence of a tool layer, which the v2 ADRs collectively cure.
