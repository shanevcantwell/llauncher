# ADR-027: Model Validate — a Single Read-Only Validation Path Reused Everywhere

**Status:** Accepted (ratified 2026-08-25 on #475)
**Date:** 2026-08-25
**Tracking:** #475 (implementing issue). Related: #468 (the "delete entries with missing weights" workflow this verb feeds), #471 (Windows cp1252 glyph fix — this verb is deliberately independent of its landing), #309/#466 (cold GET /status scan economics — the reason validation stays off the `/models` hot path).
**Doctrine:** `CLAUDE.md` — no-backcompat-shims (`PARSE-AT-THE-DOOR`); `docs/ARCHITECTURE.md` — downward-only layering, `models/` is the floor.
**Disposition:** `auto:fix` — ratified before implementation began; implemented end to end in the same PR that adds this file.

---

## Context

Three of the four pieces this verb needs already existed, scattered:

- `core/model_health.py::check_model_health(path) -> ModelHealthResult` —
  exists/readable/size, symlink-resolved, 60 s TTL cache.
- `operations/preflight.py::default_model_health_check` /
  `default_vram_check` — adapt the above and `core/gpu` into the
  `PreflightCheck` seam `(ModelConfig) -> (ok, reason)`; reachable only from
  `start`/`swap`.
- `agent/routing.py` served `GET /models/health` and
  `GET /models/health/{name}` (ADR-005) with no in-repo consumer —
  `remote/node.py` never called them.
- `ui/tabs/model_registry.py` imported `core.model_health` directly and
  re-derived its own verdict vocabulary (`missing` / `ready` / `corrupted` /
  `unknown`).

The gap was not "no checker" — it was that the verdict logic was forked
three ways (preflight adapters, the ADR-005 endpoint, the UI tab) and none
of it was reachable from the CLI.

**Shard-resolution defect (load-bearing precondition for #468).**
`ModelConfig.model_exists` resolved the sharded form
(`...-00001-of-00003.gguf` -> base `.gguf`); `check_model_health` did not —
it stat'd the literal path. A sharded entry therefore reported "not found"
from the health checker while `ModelConfig` construction saw it as valid,
and #468's "entries with missing weights are deleted" rule would have
deleted a genuinely good entry on that false negative.

## Decision

One verb, `operations.validate_models()`, built on the **existing**
preflight adapters. No new checker, and no fourth verdict vocabulary.

### Precondition — shard resolution hoisted to the floor

`llauncher/models/config.py::resolve_shard_path()` is the single source of
truth for the `-of-` shard fallback. Both `ModelConfig.model_exists`
(construction-time validation) and `core/model_health.py::check_model_health`
(runtime file stat) call it — `core -> models` is a downward edge, so this
is a legal hoist, not a layering violation. Regression-tested: a sharded
path validates OK from both call sites; a genuinely missing non-sharded
path fails from both.

### Result type — `llauncher/models/validation.py`

The floor: imports `pydantic` and stdlib only.

```python
class ValidationVerdict(BaseModel):
    check: str                 # "weights" | "gguf_magic" | "vram" | "lockfile"
    ok: bool
    reason: str = ""           # empty iff ok
    advisory: bool = False     # True => reported, does NOT gate `ok`

class ModelValidation(BaseModel):
    name: str
    model_path: str            # as configured
    resolved_path: str | None  # after symlink + shard resolution
    exists: bool = False
    size_bytes: int | None = None
    last_modified: datetime | None = None
    running_port: int | None = None
    verdicts: list[ValidationVerdict] = []
    ok: bool = False           # all non-advisory verdicts ok

class ValidationReport(BaseModel):
    checked_at: datetime
    ok: bool                   # all(m.ok for m in models)
    models: list[ModelValidation]
```

`ModelHealthResult` stays where it is and stays the mechanism;
`ModelValidation` is the envelope every door serves.

### Verb — `llauncher/operations/validate.py`

A new sibling module (not folded into `preflight.py` — that module is the
seam-adapter home shared by `start` and `swap`; every actual verb has its
own module). `validate_models(names=None, *, vram=True) -> ValidationReport`
reads `ConfigStore` and `lockfile.list_lockfiles()`; writes nothing — no
config, no lockfile, no audit entry, no reconcile.

### Doors

- **`cli.py`** — `model validate [NAME] [--json]`. Distinct from the
  existing `config validate NAME` (schema-only round-trip). Exit `0`
  all-good, `1` unknown name, `2` at least one gating failure.
- **`agent/routing.py`** — `GET /models/validate` and
  `GET /models/validate/{name}`, **replacing** (not aliasing)
  `GET /models/health[/{name}]`: no in-repo consumer, and two endpoints
  serving overlapping shapes of one artifact is the dual-shape the
  no-shims rule forbids. Plain `def`, not `async def` — the op does
  blocking file stats. **Not** folded into `GET /models`: that endpoint
  is on the UI hot path (`RemoteAggregator.get_all_models`, called every
  Streamlit rerun per node); validation stays an explicit, separately
  cacheable call, pinned by a test asserting `GET /models` performs zero
  `check_model_health` calls.
- **`mcp_server/tools/models.py`** — `validate_models` tool, peer to
  `list_models` / `get_model_config`.
- **`ui/tabs/model_registry.py`** — stops importing `core.model_health`
  and deriving its own status vocabulary; calls `ops.validate_models()`
  for `target == "local"` and `RemoteNode.get_model_validation()` /
  `RemoteAggregator.get_validation()` for a remote node. The status badge
  is a direct function of each entry's `verdicts`, not a second copy of
  the rule.

## Semantics

| check | source | gates `ok`? | notes |
|---|---|---|---|
| `weights` | `preflight.default_model_health_check` | **yes** | exists / readable / >=1 MiB, symlink- and shard-resolved |
| `gguf_magic` | first 4 bytes == `b"GGUF"` | **yes** | read inside the readability check's own `open()`; skipped for non-`.gguf` |
| `vram` | `preflight.default_vram_check` | no — advisory | skipped entirely for a currently-running model (its own weights already occupy the VRAM being compared against); suppressible via `vram=False` |
| `lockfile` | `lockfile.list_lockfiles()` + `is_pid_alive` | no — advisory | a stale claim is reported, never reconciled — `stop`/`delete` own reconciliation |

**Explicitly out of scope:** no auto-removal (deletion stays `model
remove` / `ops.delete_model`, operator-driven, not even behind a flag), no
process start, no config rewrite, no reconciliation, no audit entries (a
read emits none — matches the #463 falsifier's zero-new-audit-lines
expectation).

## Windows / cp1252 (#471)

`model validate` prints ASCII-only status tokens unconditionally (`OK`,
`MISSING`) in the CLI table — no glyph anywhere on that path, so the verb
is independent of #471's landing. Streamlit stays UTF-8 and keeps its
emoji badges.

## Consequences

- `GET /models/health` and `GET /models/health/{name}` (ADR-005) are
  removed in this PR. ADR-005's endpoint section is marked superseded by
  this ADR.
- One verdict vocabulary crosses CLI, HTTP, MCP, and UI — the fork this
  ADR closes cannot silently reopen without touching all four doors at
  once (they share `models/validation.py`).
