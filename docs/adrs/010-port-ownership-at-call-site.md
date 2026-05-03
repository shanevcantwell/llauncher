# ADR-010: Port Ownership at the Call Site

**Status:** Accepted  
**Date:** 2026-05-02  

## Context

`PRODUCT_REQUIREMENTS.md` §2.1 places `default_port: int | None = None` on `ModelConfig` — a per-model field describing the model's preferred listening port. Auto-allocation (§4.2's `find_available_port`) fills in a free port from the 8081–8999 range when `default_port` is unset or unavailable. ADR-004 confirms the CLI signature `llauncher server start <model> [port]` and recommends "optional, default to auto."

The result is that **the port a model ends up running on is determined by a chain of fallbacks**:

1. Caller's explicit `port` argument (if given), then
2. `model.default_port` (if set), then
3. Auto-allocation in a hardcoded range.

This conflates two concerns:

- **What to load** (model identity: weights, params, sampling) — properly belongs to `ModelConfig`.
- **Where to run it** (port assignment) — a deployment-time decision, not an attribute of the model.

Symptoms of the conflation:

- A `ModelConfig` carries a port even when no instance is running. The field implies state that doesn't exist.
- Two callers wanting to run the same model on different ports (parallel testing, distinct slots) need either separate configs or per-call overrides — but the override semantics are buried in the fallback chain.
- The `running` dict is keyed by port (`dict[int, RunningServer]`), and lockfiles (per ADR-008) are keyed by port. The port is already the primary key for runtime state. Only `ModelConfig` clings to it.
- "Where did my model end up?" has three possible answers and no obvious one.

The scoping conversation for v2 settled this directly: *"the port is being stored in the wrong layer... It really belongs in the call, not the config."*

## Decision

### Option Chosen: Port is a Required Call Parameter; ModelConfig is Port-Agnostic

`ModelConfig` no longer carries port information. Every operation that affects a running server takes `port` as an explicit, required argument at the API boundary.

### Schema Change

`ModelConfig.default_port` is **removed**. On load of an existing `config.json`, the field is silently dropped (per the v2 migration policy: "the data isn't precious; user re-specifies").

### Endpoint Shape

The HTTP Agent's start/stop/swap endpoints become uniformly port-keyed:

| Operation | Endpoint | Body |
|-----------|----------|------|
| Start | `POST /start/{port}` | `{model: str}` |
| Swap | `POST /swap/{port}` | `{model: str}` |
| Stop | `POST /stop/{port}` | (none) |

The three verbs are deliberately distinct, not interchangeable. Each fails on the wrong precondition rather than silently doing another verb's job. The verb's failure mode is about catching a wrong **caller mental model**, not about counting transitions.

**Start** (port + model) — caller expects the port to be free:

| Port state | Outcome |
|------------|---------|
| Empty | success |
| Same model already running | success |
| Different model running | failure (`port_occupied_by_other`) |

**Swap** (port + model) — caller expects the port to be occupied:

| Port state | Outcome |
|------------|---------|
| Empty | failure (`port_empty`) |
| Same model already running | success |
| Different model running | success |

**Stop** (port only):

| Port state | Outcome |
|------------|---------|
| Empty | success |
| Occupied | success |

The model-keyed `POST /start/{model}` endpoint is **removed**. The model-keyed `/start-with-eviction/{model}` is removed in favor of `/swap/{port}` (see ADR-011).

This is a breaking change for any client that currently uses the model-keyed start path. Single-user scope: clients are the CLI, MCP server, and pi-coding-agent — all under the same hands.

### MCP Tools

Mirrors the HTTP shape. Every relevant tool takes `port` as a required parameter:

- `start_server(model, port)`
- `swap_server(port, model)`
- `stop_server(port)`

### Tool Prompt Guidance

The MCP tool descriptions seen by LLMs should make the use case explicit so the model picks the right verb without guessing. Sketches:

- **swap**: "Replace the model on this port with a different one. Primary use: an agent replacing its own brain on the harness's expected port. Calling with the model already running is a successful no-op."
- **start**: "Start a model on an empty port. Fails if anything is already running there."
- **stop**: "Stop whatever is on this port. Success if nothing was there."

Response shape carries machine-readable outcome flavor for the harness or the next LLM turn:

```json
{
  "success": true,
  "action": "started" | "swapped" | "stopped"
          | "already_running" | "already_empty",
  "port": 8081,
  "model": "mistral-7b",
  "pid": 12345
}
```

`success` stays binary; the harness uses it to know whether to retry. `action` distinguishes outcomes; the LLM (via the harness) reads it to know what actually happened on the port.

### CLI

```
llauncher server start <model> --port <port>
llauncher server stop <port>
llauncher server swap <port> <model>
```

`--port` may default from a `DEFAULT_PORT` env var. The CLI synthesizes an explicit port before issuing the API call; the API always sees an explicit value.

If `--port` is unset and `DEFAULT_PORT` is unset, the CLI errors with a clear message. Auto-allocation in a hardcoded range is **not** an API-level behavior; if a user wants it, the CLI can offer `--auto-port` as an explicit opt-in (see Open Questions).

### Validation

`ChangeRules.validate_start(config, caller, port)` already takes `port`. Signature unchanged. The blacklisted-ports check (`{8080}` by default) continues to operate at this layer — orthogonal to where the port came from.

### Lockfile and Audit

No change. Both are already keyed by port (per ADR-008). This ADR is what makes the lockfile-port-keying natural rather than coincidental.

### Considered but Not Implemented: Restart

A `POST /restart/{port}` verb was considered for use cases like picking up config changes, recovering from suspected `llama-server` runtime issues, or refreshing the KV cache without changing models. Mechanically it would be a stop + same-port start, with the model name read from the lockfile.

**Deferred — too marginal a use case for this phase.** If a user genuinely needs to tear down and re-launch the same model, two calls (`stop` then `start`) accomplish it. Revisit only if a real ergonomic complaint surfaces.

## Consequences

**Positive:**

- One source of truth for "where this model runs": the caller's explicit port. The fallback chain disappears.
- `ModelConfig` becomes a pure description of model identity and load parameters. Forms get smaller; validation simpler.
- Same model can run on multiple ports without config duplication.
- Eliminates an entire category of "where did this model end up?" surprise.
- Aligns the API contract with the runtime state model — port is the primary key throughout.

**Negative:**

- Breaking change to the HTTP API surface and CLI signature. Single-user scope absorbs this; not a concern.
- CLI ergonomics shift: users who relied on "just type the model name and let llauncher figure it out" now need either a `DEFAULT_PORT` env or an explicit `--port`. Trade-off: predictability over typing economy.
- The "next free port" convenience is no longer free at the API layer. UX layers can offer it; infra cannot assume it.

**Open Questions:**

1. The `port` query parameter that some clients currently pass to `/start-with-eviction` is being eliminated; verify no external client (notably pi-coding-agent's TypeScript extension from ADR-001) depends on the model-keyed form.

**Closed during drafting:**

- *Should the CLI ship `--auto-port`?* No. "Set `DEFAULT_PORT` or specify explicitly" is sufficient for the single-user hobby scope. Revisit only if a real ergonomic complaint surfaces.
- *Should `/start/{port}` and `/swap/{port}` merge?* No. The verbs catch different mental-model errors and should fail loudly on the wrong precondition rather than silently doing each other's work.

## Supersession

- Supersedes the `default_port` field in `PRODUCT_REQUIREMENTS.md` §2.1.
- Supersedes the auto-allocation fallback chain in §4.2 at the API level (CLI may still implement opt-in auto-pick).
- Supersedes ADR-004's Open Question 1 ("auto-assign or require explicit?") with the explicit-required answer.
- Removes the model-keyed `POST /start/{model}` endpoint and the `/start-with-eviction/{model}` endpoint described in §5.2.

## Relationship to Other ADRs

- **Builds on ADR-008** (stateless facade): the lockfile and `running` dict are already port-keyed; this ADR finishes the alignment by removing the only remaining port-coupled-to-config artifact.
- **Builds on ADR-009** (symmetric topology): port resolution is local to the target node; no cross-node port coordination is needed because nothing in this layer crosses nodes.
- **Constrains ADR-011** (Swap Semantics, forthcoming): `/swap/{port}` shape is set here; the swap rules and concurrency semantics are 011's job.
- **Supersedes part of ADR-004** (CLI Subcommand Interface): the port-optional CLI signature changes.
