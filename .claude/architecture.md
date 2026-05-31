# llauncher — Layer Architecture (current)

> Minimal distillation of the layering doctrine for use *at edit time*.
> Deeper context: ADRs (esp. ADR-008 stateless facade, ADR-010 port-keyed
> endpoints) and the historical `docs/1-architecture-layers.md` /
> `docs/2-cross-layer-reach.md`.

## The layers

```
ENDPOINT        agent/ (HTTP)   mcp_server/ (stdio)   ui/ (Streamlit)   cli.py
                     │                 │                   │             │
                     └─────────────────┴────────┬──────────┴─────────────┘
                                                ▼
ORCHESTRATION   operations/  (stateless verbs: start · stop · swap · delete · orphan · preflight)
                state.py     (LauncherState facade — ADR-008)
                                                ▼
CORE            core/  (config · process · settings · lockfile · audit_log · model_health)
                                                ▼
MODELS          models/  (pydantic data types — the floor)

REMOTE (client) remote/  (NodeRegistry · RemoteNode · RemoteAggregator)
                used by ui/ and cli;  reaches a node ONLY over HTTP to agent/ endpoints.
```

## The one rule

**Dependencies point downward. Siblings do not import siblings.**

- Endpoints orchestrate; orchestration uses core; core uses models.
- `remote` and `agent` are **peers across the network boundary** — they share
  the HTTP wire contract and nothing else. `remote` is a client; `agent` is a
  server. Neither imports the other in Python.
- If two siblings need the same helper, **hoist it down** into a shared lower
  layer (`core`), don't reach sideways.

## Forbidden edges (guard these)

| Edge | Why it's wrong | Do instead |
|---|---|---|
| `remote → agent` | client importing its server | call over HTTP, or hoist the shared helper into `core` |
| `core → {state, operations, agent, remote, ui, mcp_server}` | core must not import upward | invert: caller passes what core needs |
| `models → anything` | data types are the floor | keep them dependency-free |
| `state`/`operations` → endpoint layers | orchestration must not know its callers | endpoints depend on orchestration, never the reverse |

> Known regression: `remote/registry.py` imported `agent.auth.resolve_agent_token`
> (a `remote → agent` edge) to source the local token. The fix is to hoist the
> token *read* path into `core` and keep token *materialization* in `agent`.
