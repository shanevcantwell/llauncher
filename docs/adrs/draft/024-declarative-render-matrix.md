# ADR-024: Declarative Render Matrix — internal config → backend argv as a typed table

**Status:** Draft
**Date:** 2026-06-29
**Tracking:** #156 (direct motivator — contract gap + silent flag-collision loss). Related: #184 (promote risky `extra_args` flags; keep its mechanical half, drop its semantic-gate half), #152 (schema as boundary layer), #155 (vLLM/ST backend kind — future column), #170 (the `models → core` layering wart NOT to deepen).
**Doctrine:** `harness-tools/docs/ground-physics/GROUND_PHYSICS.md` + `CODE_CONSTITUTION.md` — `IDENTITY⊥ENVELOPE`, `ONE-MINT`, `EMIT-CANONICAL`, `PARSE-AT-THE-DOOR`, `ONE-DOOR`. Layering: `docs/ARCHITECTURE.md` rule 1 (downward-only).
**Disposition:** `auto:draft` — operator ratifies before any implementation begins. In-document Status `Draft` is the house canon (README §statuses) for the dispatch's "Proposed."

---

## Context

llauncher's internal-config → backend-argument mapping lives today as one imperative
function, `core/process.py::build_command` (lines 80–185). It is a flat sequence of
`cmd.extend([...])` calls, each hand-coding four orthogonal concerns inline and
invisibly:

- **arg spelling** — `"-c"` for ctx, `"--n-gpu-layers"`, `"--flash-attn"`;
- **cardinality** — value-flag (`--ctx-size N`), boolean-presence (`--no-mmap`,
  `--mlock`), enum-as-value (`--flash-attn on|off|auto`);
- **omission semantics** — scattered `if config.x is not None:` / `if config.x:`
  guards (`--mmproj`, `--threads`, `--batch-size`, `--cache-type-k/v`, `--n-cpu-moe`,
  sampling params, `--parallel` with a `> 1` guard);
- **provenance** — which flags llauncher *forces* and a config may never override.
  This is encoded **separately**, in `models/config.py::DENIED_EXTRA_ARG_FLAGS`
  (lines 70–77: `--api-key`, `--alias`, `-m`/`--model`, `--host`/`--port`).

This imperative shape has produced three concrete, anchored defects:

1. **Silent field drop (live bug).** `ModelConfig.np` (config.py:111, "Number of KV
   cache pages") is a canonical typed field that `build_command` **never reads** — it
   has no `cmd.extend` line. A setting an operator can write is silently discarded at
   render time. Nothing in the imperative form makes "every field is rendered or
   explicitly unsupported" checkable; the omission is invisible until someone diffs the
   field list against the argv by hand.

2. **The `#156` second door.** The MCP `update_model_config` tool maintains a
   *second*, hand-curated writable allow-list that is far narrower than the field set —
   `mcp_server/tools/config.py` accepts only `n_gpu_layers`, `ctx_size`, `threads`,
   `flash_attn`, `no_mmap`, `extra_args` (inputSchema lines 27–32; apply-block lines
   146–157). To set `cache_type_k`, `parallel`, `n_cpu_moe`, or any sampling param an
   operator must reach **around** the typed door via `extra_args` free-text. Two
   independently-maintained lists (the renderer's fields and the tool's allow-list) is
   a `ONE-DOOR` violation: they drift, and the gap forces the very `extra_args`
   reach-around the deny-list exists to discourage.

3. **Silent collision / trust-and-degrade (`#156`).** `extra_args` is `shlex.split`
   and appended last (process.py:181–183). `DENIED_EXTRA_ARG_FLAGS` catches the
   identity/security flags, but **not** envelope flags: `extra_args="-c 4096"` together
   with the `ctx_size` field emits `-c` twice, and llama-server silently resolves the
   duplicate. That is a `PARSE-AT-THE-DOOR` violation — the door accepts a contradictory
   argv and degrades instead of failing loud.

These are not three features; they are three symptoms of one missing structure. The
ecosystem doctrine names the remedy directly: `EMIT-CANONICAL`/`PARSE-AT-THE-DOOR` say
*push the invariant onto a schema where the substrate allows* — strongest when
grammar-enforced, weakest when prose-only. Moving the mapping from imperative code
(prose-grade) to a declarative table **is** that enforcement-surface upgrade. This ADR
is therefore framed as **conformance work, not a new feature**.

`ModelConfig.kind` (config.py:80–88, `BackendKind`, today only `LLAMA_SERVER`) is the
*sanctioned* axis along which the mapping varies. `IDENTITY⊥ENVELOPE` makes `kind` part
of the model-reference identity, orthogonal to the endpoint; the settings it governs are
**envelope**. So the natural shape is a matrix: settings on one axis, backend `kind` on
the other.

## Decision

Replace the imperative `build_command` mapping with a **declarative render matrix**: a
core-owned, typed registry that renders `ModelConfig` → backend argv as data, not
control flow.

1. **Rows** = canonical settings, keyed by `ModelConfig` field **name** (a string). The
   table holds field *names*, not field *objects*, so `models/` stays a dumb pydantic
   floor and the edge points **down** (`core` reads `models`), never up — `#170` is not
   deepened (see Layering below).

2. **Columns** = `BackendKind`. Today one column (`llama_server`); vLLM /
   SentenceTransformers (`#155`) are future columns added as *data*, not code.

3. **Cells** = a render **spec**, not a string. A cell can express all of:
   - **arg spelling** (`-c`, `--ctx-size`, `--served-model-name`);
   - **value transform** — e.g. llama `-c` total-context vs a vLLM per-sequence
     `max_model_len`; `flash_attn` enum `on|off|auto` (value-flag) vs a hypothetical
     boolean-presence flag on another backend;
   - **cardinality** — `VALUE_FLAG` | `BOOL_PRESENCE` | `REPEATABLE`;
   - **omission semantics** — `None` → emit nothing (this *replaces* the scattered
     `if config.x:` guards);
   - **unsupported** — the cell can declare a setting has no slot on this backend (e.g.
     llama `--parallel`/`-np` has no direct vLLM equivalent), subject to a once-decided
     policy (Open Question 2);
   - **provenance** — `FIELD_DERIVED` | `LAUNCHER_FORCED` | `PASSTHROUGH` | `DENIED`.

4. **The `name` row is special — identity, not envelope.** It renders the canonical
   `ModelConfig.name` **untransformed** in *every* column (`--alias` for llama,
   `--served-model-name` for vLLM), provenance `LAUNCHER_FORCED`, non-overridable. The
   existing `DENIED_EXTRA_ARG_FLAGS` set collapses into cell provenance: `--alias`,
   `--api-key`, `-m`/`--model`, `--host`/`--port` become `LAUNCHER_FORCED` /
   `DENIED` cells. This **strengthens** `EMIT-CANONICAL` — it becomes a per-column
   property a test can assert across all backends, rather than a single hard-coded line
   that a second backend could forget. It must not weaken it.

5. **The MCP writable allow-list is derived from the matrix.** A field is writable via
   `update_model_config` iff its cell provenance for that `kind` is `FIELD_DERIVED`. The
   hand-maintained list in `mcp_server/tools/config.py` is **deleted, not extended** —
   `ONE-DOOR`. The MCP `inputSchema` and the apply-block are generated from the same
   table the renderer uses.

6. **A structural collision check fails loud at the door.** Before appending
   `extra_args`, render computes the set of flag-spellings the matrix will emit for this
   `kind`; any `extra_args` token whose flag-head collides is rejected with an
   at-the-door error naming **both** sources. This closes `#156`'s silent-loss path
   under `PARSE-AT-THE-DOOR`.

### Explicit non-goal (the line, stated loudly)

**The matrix encodes ZERO semantic compatibility knowledge.** It is a *mechanical /
structural* mapping: internal setting → backend argument. A cell may **render** a value
or **declare-unsupported**. A cell may **not** encode "…but not if some *other* setting
is set" (e.g. "MTP is incompatible with `parallel > 1` for this model arch"). That
direction — a model-physics oracle keyed on arch × flag — is a deliberately **rejected**
design (an unbounded maintenance black hole; see Alternatives). This is precisely the
half of `#184`'s structural suggestion we **drop**; we keep its mechanical half (promote
risky flags to typed fields, Phase 2).

The boundary is sharp and worth restating because it will be tested under pressure:
- **In scope** — *structural* validity: a cell exists for every field; the same flag is
  not set two ways (collision detection, decision §6). This is schema well-formedness.
- **Out of scope** — *semantic* validity: whether two individually-valid settings are
  compatible for a given model architecture. That stays the operator's (and
  llama-server's own argv parser's) concern.

### Cell schema (interface sketch — not implementation)

```python
# core/render_matrix.py  (CORE layer; reads models/ floor by field-NAME string)

class Cardinality(Enum):
    VALUE_FLAG     # --flag VALUE
    BOOL_PRESENCE  # --flag  (emitted iff truthy; no value)
    REPEATABLE     # --flag V1 --flag V2 ...

class Provenance(Enum):
    FIELD_DERIVED   # from a ModelConfig field; operator-writable via the door
    LAUNCHER_FORCED # llauncher owns it (e.g. --alias=name); never operator-set
    PASSTHROUGH     # the extra_args escape hatch
    DENIED          # must never appear (e.g. --api-key from a field/passthrough)

class RenderSpec(FrozenModel):
    spelling: str                       # "-c", "--served-model-name", ...
    cardinality: Cardinality
    provenance: Provenance
    transform: Callable[[Any], Any] | None   # default identity; declarative where possible
    # unsupported is represented by a distinct sentinel cell, not a flag here:

class Unsupported(FrozenModel):
    reason: str                         # e.g. "no per-request parallel slot on vLLM"
    policy: UnsupportedPolicy           # REJECT | WARN | IGNORE  (Open Question 2)

Cell = RenderSpec | Unsupported

# The matrix: column(kind) -> { field_name: Cell }
MATRIX: dict[BackendKind, dict[str, Cell]]
```

### Render pipeline (pseudocode — argv-identical to today for the happy path)

```
def render_argv(config, kind, runtime):        # runtime carries port/host
    column = MATRIX[kind]
    assert_every_field_has_a_cell(config, column)   # structural-completeness gate
    argv = [binary]
    emitted_flag_heads = {}                    # spelling -> source field (collision map)
    for field_name, cell in column.items():
        if isinstance(cell, Unsupported):
            apply_unsupported_policy(cell, config, field_name)   # reject | warn | ignore
            continue
        if cell.provenance == DENIED:
            continue
        value = resolve(config, field_name, runtime)   # runtime for name/host/port-forced
        tokens = cell.render(value)            # [] when None  (replaces `if config.x:`)
        argv += tokens
        record_flag_head(emitted_flag_heads, cell.spelling, field_name)
    # PASSTHROUGH last — but FIRST fail loud on structural collision (#156):
    for tok in shlex.split(config.extra_args):
        head = tok.split("=", 1)[0]
        if head in emitted_flag_heads:
            raise AtTheDoorError(f"{head!r} set both by field "
                                 f"{emitted_flag_heads[head]!r} and extra_args")
        if head in denied_heads(column):       # absorbs DENIED_EXTRA_ARG_FLAGS
            raise AtTheDoorError(...)
    argv += shlex.split(config.extra_args)
    return argv
```

## Rationale

### Positive consequences
- **The three anchored defects close structurally**, not by patch: the `np` silent drop
  (completeness gate makes it a visible `Unsupported`/`FIELD_DERIVED` decision), the
  `#156` second door (allow-list derived, not duplicated), and the `#156` silent
  collision (fail-loud at the door).
- **`EMIT-CANONICAL` is upgraded from a line to an invariant** — a per-column test
  asserts the `name` cell is `LAUNCHER_FORCED` and untransformed in every backend, and
  `--api-key` is `DENIED` everywhere. The substrate enforces what was prose.
- **A new backend (`#155`) is a data addition**, not a re-architecture, *provided* the
  cell schema carries transform/cardinality/unsupported/provenance from day one (the
  whole point of designing the cell now — Phase 3).
- **`models/` stays a dumb floor.** The table is core data keyed by floor field-names; no
  new `models → core` edge.

### Negative consequences
- **One indirection added** between a field and its flag. Reading "what flag does
  `ctx_size` become" now means consulting a table, not a line in a function. Mitigated by
  the table being one flat, greppable, auditable surface — which is the trade we want.
- **The collision check is the one intentional behavior change in Phase 1** (see
  Phasing): a config that today silently emits a duplicate flag will, after this, be
  *rejected* at the door. That is the `#156` fix, but it can surprise an operator whose
  `extra_args` overlapped a field. Mitigated by a precise error naming both sources and a
  migration note. Everything *else* in Phase 1 is argv-identical.
- **Matrix completeness becomes a maintenance obligation**: a new `ModelConfig` field
  with no cell fails the completeness gate. This is a feature (it would have caught `np`),
  but it is a gate contributors must satisfy.

## Alternatives considered

### A. Keep the imperative `build_command` (status quo)
**Rejected.** It is the source of all three anchored defects. It cannot express
provenance in the same place as spelling (the deny-list lives in a different module from
the renderer), has no completeness check (hence the `np` drop), and grows a deeper
conditional thicket with every backend. Simpler to leave alone *today*, but the cost is
paid continuously and compounds at the first second backend.

### B. Semantic-gating matrix (cells encode arch × flag compatibility)
**Rejected — the explicit non-goal.** Letting a cell say "valid only if setting Y is
unset for arch Z" turns the table into a model-physics oracle: an unbounded,
per-architecture, per-llama.cpp-version knowledge base that must be maintained against an
upstream that changes weekly. The maintenance surface is effectively infinite and the
failure mode (stale gate rejects a now-valid combo) is worse than no gate. We keep
*structural* collision detection (same flag two ways — finite, decidable) and refuse
*semantic* gating. This is the line `#184` blurred; we draw it.

### C. Polymorphic per-backend renderer classes (OO subclassing of `build_command`)
**Rejected.** It scatters the mapping across N renderer classes, one per backend, and
loses the single auditable table. The completeness check ("every field has a cell in
every column") and the cross-backend `EMIT-CANONICAL` assertion both become awkward
when the mapping is distributed across class hierarchies rather than centralized as data.
The value of this ADR is *one declarative surface*; OO polymorphism trades it away.

### D. Cell = plain format string (`"--ctx-size {}"`)
**Rejected.** A string cannot carry value-transform (llama `-c` total vs vLLM
`max_model_len` per-sequence), cardinality (bool-presence vs value vs repeatable),
unsupported-declaration, or provenance. The transform requirement alone forces a spec
object. A string would re-create the imperative problem one level down.

### E. Externally hot-reloaded matrix data (matrix as config file, not in-code registry)
**Rejected for now** (recorded as Open Question 1b). Making the matrix externally
editable data multiplies the maintenance/validation surface (now the *table itself* is a
persisted artifact needing parse-at-the-door, schema validation, and migration) for a
benefit — operator-tunable arg mapping — nobody has asked for. The in-code registry is
the floor; revisit only if a concrete need appears.

## Phasing (waterfall-friendly; each phase independently shippable; risk/scope-first)

### Phase 1 — Behavior-preserving refactor + close `#156` structurally
**Delivers:** `core/process.py::build_command` reimplemented as `render_argv` over the
matrix, **`llama_server` column only**. For valid configs the emitted argv is
**byte-identical** to today (the existing `process.py` test suite is the regression net;
add an argv-golden test if one is absent). Then, in the same phase:
- Derive the `update_model_config` writable allow-list from the matrix and **delete** the
  hand-maintained list in `mcp_server/tools/config.py` (lines 27–32 schema, 146–157
  apply-block) → closes `#156`'s second-door structurally.
- Add the door collision check (decision §6) — the one intentional behavior change, gated
  behind a clear at-the-door error.
- Encode `ModelConfig.np`'s current behavior honestly (today: silently unrendered) as an
  explicit cell and flag it for Phase 2 resolution — *do not* silently start emitting it
  (that would break behavior-preservation); make the existing defect *visible*.
**Depends on:** nothing. **Verifiable when it lands:** argv-golden suite green; the MCP
tool exposes exactly the `FIELD_DERIVED` fields; a duplicate-flag config is rejected with
a both-sources error; a completeness test fails if any field lacks a cell.

### Phase 2 — Promote hazardous/missing `extra_args` flags to typed canonical fields
**Delivers:** the flags currently reachable only via `extra_args` free-text become typed
`ModelConfig` fields with `FIELD_DERIVED` cells: `#184`'s cache-reuse flag; the
spec-draft family (`--spec-type`, `--spec-draft-n-max`); the batch/parallel family from
`#156`. Each promotion ships with a **one-time deterministic at-the-door migration** of
persisted configs (`PARSE-AT-THE-DOOR`: rewrite in place once, no dual-parse, no shim — a
flag present in a stored config's `extra_args` is lifted to its new typed field on load).
`extra_args` **remains** an escape hatch, but the tabled flags are no longer the *only*
way to set those behaviors. Also resolve the `np` defect surfaced in Phase 1 (map it,
unify with `parallel`, or formally mark unsupported — an operator decision).
**Depends on:** Phase 1 (the matrix + provenance machinery). **Verifiable when it lands:**
a persisted config carrying a promoted flag in `extra_args` migrates to the typed field on
load; the field is writable through the MCP door; no dual-parse path exists.

### Phase 3 — First real second column (vLLM, `#155`)
**Delivers:** the `vllm` `BackendKind` column, added when the `#155` roadmap firms. The
cell schema designed in Phase 1 must already carry value-transform / cardinality /
unsupported so this is a **data addition, not a re-architecture**. **Before** Phase 1
freezes the cell schema, validate it *on paper* against 3–4 real vLLM settings —
`max_model_len` (per-sequence transform of llama's total `-c`), `tensor-parallel-size`
(no llama equivalent), the `-np`/`--parallel` hole (an `Unsupported` cell on vLLM), and
an enum-style flag — to confirm the cell can express them. **Do not** build the vLLM
renderer before vLLM exists (the YAGNI line). **Depends on:** Phase 1 (schema) + the
`#155` decision. **Verifiable when it lands:** a `kind=vllm` config renders a correct
vLLM argv with the canonical name emitted via `--served-model-name`, untransformed.

## Open questions

- [ ] **Hot-swappability (operator-named, scope unresolved).** Three distinct readings,
  recorded so they are not conflated: **(a)** swap *backend* under a fixed canonical
  config (re-render the same `ModelConfig` for a different `kind`); **(b)** the *matrix
  itself* as externally hot-reloaded data vs an in-code registry (Alternative E); **(c)**
  live re-render + relaunch on a single setting change. **Recommendation:** (a) is the
  load-bearing one and the matrix shape directly enables it; pursue it. **Caution against
  (b)** — see Alternative E (it makes the table a persisted artifact with its own
  parse/validate/migrate burden). (c) is a `swap`-semantics question (ADR-011), largely
  orthogonal to this matrix. **Resolution:** operator picks the target reading before
  Phase 3; (a) needs no extra work, (b)/(c) are separate ADRs if wanted.
- [ ] **`Unsupported`-cell policy: reject vs warn vs ignore.** A one-time decision for the
  default. *reject* = fail loud at the door (most `PARSE-AT-THE-DOOR`-aligned); *warn* =
  emit nothing but log; *ignore* = silently drop (today's de-facto behavior for `np`,
  which we judged a defect). **Recommendation:** default *reject*, with per-cell override
  available. **Resolution:** operator ratifies the default before Phase 1 codes the
  `apply_unsupported_policy` branch.
- [ ] **Canonical vocabulary: llama-shaped-with-translating-cells vs neutral rename.**
  Keep field names llama-shaped (`ctx_size`, `flash_attn`) and let cells translate per
  backend (**lower churn — recommended**), or rename fields to backend-neutral terms as
  backends multiply (higher churn, touches every persisted config and consumer).
  **Resolution:** default to translating-cells; revisit only if a third backend makes the
  llama-shaped names actively misleading.

## Layering & doctrine compliance (the spine)

| Doctrine handle | How this ADR honors it |
|-----------------|------------------------|
| `IDENTITY⊥ENVELOPE` | `kind` is the sanctioned column axis; the matrix renders the **envelope** (settings) per `kind` while **identity** (`name`) is held invariant across columns. |
| `ONE-MINT` + `EMIT-CANONICAL` | The `name` cell is `LAUNCHER_FORCED`, untransformed, in every column (`--alias` / `--served-model-name`). `DENIED_EXTRA_ARG_FLAGS` collapses into cell provenance. The invariant is *strengthened* (per-column test), never weakened. |
| `PARSE-AT-THE-DOOR` | The structural collision check fails loud instead of trust-and-degrading on a duplicate flag (`#156`). Phase 2 migrations rewrite persisted configs in place, once — no dual-parse, no shim (project `CLAUDE.md` local rule). |
| `ONE-DOOR` | `update_model_config`'s writable set is *derived from* the matrix, not a second hand-maintained list. Delete, don't extend. |
| Layering (ARCHITECTURE.md rule 1) | The matrix lives in `core/`, reads the `models/` floor **downward**, keyed by field-**name** strings. It must **not** add a `models → core` edge — `#170` (`config.py:19` already illegally imports `core.settings`) is not deepened; `ModelConfig` stays a dumb pydantic floor. |
| Opinion locality (`CODE_CONSTITUTION`) | The matrix is a substrate/data-plane mechanic (llama argv rendering). It lives in llauncher `core`; it must **not** tempt a new invariant into `GROUND_PHYSICS`. The constitution stays short. |

## Risk & observability

- **Risk — the refactor touches the most safety-critical function in the codebase**
  (the argv that launches GPU processes). *Mitigation:* Phase 1 is behavior-preserving;
  the existing `process.py` suite plus an argv-golden test is the regression net; the
  matrix is reviewed as one flat surface.
- **Risk — collision fail-loud rejects a previously-"working" duplicate-flag config on
  upgrade.** *Mitigation:* precise at-the-door error naming both sources; document in the
  Phase 1 migration note. This is the `#156` fix surfacing, not a regression.
- **Tech-debt vector — matrix incompleteness** (a new `ModelConfig` field without a
  cell). *Mitigation:* the completeness gate (`assert_every_field_has_a_cell`) is the
  same machinery that would have caught the `np` drop; it runs at render and as a test.
- **Security — `LAUNCHER_FORCED`/`DENIED` provenance carries the `EMIT-CANONICAL` and
  `--api-key` guarantees.** A regression here weakens identity/security across *all*
  backends at once. *Mitigation:* provenance is test-asserted per column (`--alias`
  always emitted from `name`; `--api-key` never `FIELD_DERIVED` or `PASSTHROUGH`).
- **Observability win (not required Phase 1):** render provenance is now inspectable —
  the agent/MCP surface can report which flags were field-derived vs launcher-forced vs
  passthrough for a running server, useful for the status/introspection endpoints.

## Preconditions / notes for the operator

- The **vLLM argv surface (`#155`) is not firm.** Phase 3 is paper-validation only until
  it is; this does **not** block Phases 1–2.
- **Hot-swap scope (Open Question 1)** is an operator decision; Phases 1–2 do not depend
  on it.
- This ADR is `auto:draft`: it records the *shape* of the decision. It does **not**
  enumerate per-field sub-tasks with acceptance criteria (that is a `decompose-problem`
  pass over Phase 1) nor step-level edits (that is a `plan` pass). Recommend a decompose
  pass over Phase 1 before dispatching implementation, given it spans `core/process.py`,
  `mcp_server/tools/config.py`, and a new `core/render_matrix.py`.

## Supersession relationships

**Supersedes:** none. **Superseded by:** TBD. **Amends in spirit:** ADR-007
(repeat-penalty tuning) and the sampling-param surface become `FIELD_DERIVED` cells;
no status change to those ADRs.
