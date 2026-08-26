# ADR-LLNCH-026: `ModelConfig` Does Not Mirror llama-server's Argument Schema

**Status:** Accepted (ratified 2026-08-25 on #477)
**Date:** 2026-08-25
**Tracking:** #477 (direct motivator — `cache_type_k`/`v`'s `Literal` cannot
hold `q4_0`, a value the operator's live registry already used via
`extra_args`). Amends: ADR-LLNCH-024 (Phase 2 withdrawn; Phases 1/3 re-scoped —
see the amendment note at the top of that file). Amends in spirit: ADR-LLNCH-007
(repeat-penalty tuning recommendations stand as operator guidance; the field
behind them does not).
**Doctrine:** `PARSE-AT-THE-DOOR`, `ONE-MINT`, `EMIT-CANONICAL` (project
`CLAUDE.md`); `docs/ARCHITECTURE.md` rules 4–6.
**Disposition:** `auto:fix` — ratified by the operator on issue #477
2026-08-25, with directive "disable pydantic for `extra_args`". This ADR is
created and accepted in the same PR that implements it, per the ratification
comment's point 6.

> **Amendment (ADR-LLNCH-028, 2026-08-26):** the owned column is re-opened under
> a stated admission criterion (gate / constrain / compute-from, *or* an
> operator-designated per-entry control), and every typed field carries its
> value as a primitive — never a `Literal` over a vocabulary llama-server owns.
> *Does not mirror* was never *does not type*: the two collapse into one thing
> only if a typed field re-declares upstream's value space, which ADR-LLNCH-028
> forbids outright. This ADR's `extra_args`-verbatim decision and its
> launch-time deny-list enforcement point are unchanged; the 16-field partition
> is narrowed by the flags ADR-LLNCH-028 admits and otherwise stands.

---

## Decision

**`ModelConfig` is not a mirror of llama-server's argument schema.** It is
llauncher's own state: the identity it mints, the artifacts that identity
names, and the handful of runtime properties llauncher itself reads,
reports, or enforces. Every other llama-server flag lives in `extra_args`,
verbatim, in the spelling the operator read out of `llama-server --help`.
`extra_args: str` carries those flags with **no pydantic validation of its
contents** — no shell-quoting check, no managed-flag collision guard. The
set llauncher owns is small and each member is justified by an invariant it
serves, not by convenience:

| Flag | Source | Invariant it serves |
|---|---|---|
| `--alias` | `ModelConfig.name` | **EMIT-CANONICAL** (ARCHITECTURE rule 5, #120). The wire must report the minted name byte-for-byte. Denied in `extra_args` so no config can override the minted identity. |
| `-m` / `--model` | `model_path` | **ONE-MINT envelope** (rule 4). Carries the `model_exists` path validator (shard-pattern aware); a duplicate in `extra_args` bypasses that validator. |
| `--host` / `--port` | runtime args to `build_command` | **ADR-LLNCH-010**: port/host are deployment-time, call-site concerns, never config attributes. |
| `--api-key` | (never emitted; denied) | Security boundary, #81 / security-hardening-plan §3 C7. |
| `--metrics`, `--slots`/`--no-slots` | `metrics`, `slots` | llauncher's own observability and exposure contract. `core/server_metrics.py`, `agent/routing.py`, and the MCP `servers` tools *consume* `/metrics` and `/slots`. `--slots` is additionally a security posture (ADR-LLNCH-019): llama-server's binary default is ENABLED and `/slots` leaks per-slot prompt text, so llauncher emits the flag explicitly in both directions. |
| `-c` / `--ctx-size`, `--parallel` | `ctx_size`, `parallel` | llauncher's own published **capacity contract**: `agent/footer_cache.py` holds `(model, ctx_size, parallel, port)` as the lockfile-backed footer tuple; `agent/routing.py` serves `ctx_size`/`parallel` on the footer endpoint (ADR-LLNCH-012); `operations/start.py`/`operations/swap.py` refresh it into their results (#267). Not kept because llama-server takes `-c` — kept because llauncher answers `GET /footer` with them. If the footer contract ever drops them, they drop too. |
| `--n-gpu-layers` | `n_gpu_layers` | `operations/preflight.py` reads it for VRAM admission control — llauncher makes a start/refuse decision on this value. |
| `--mmproj` | `mmproj_path` | A second on-disk artifact reference, not a tuning knob — the same kind of thing as `model_path`, half of a multimodal model's identity envelope. |

Everything else that was in `MANAGED_NATIVE_FLAG_TO_FIELD` was
llama-server's schema leaking into llauncher's floor. The
`cache_type_k`/`cache_type_v` defect in #477 is the proof by construction: a
shadow field whose `Literal` cannot hold `q4_0` is *strictly less
expressive* than the flag it shadows, and used to emit a warning
prescribing a field that cannot represent the value in use. Mirroring a
foreign schema is a debt that compounds every time upstream adds a value.

## The partition

22 map entries / 16 fields → 6 kept fields, 16 dropped:

**Dropped** (removed from `ModelConfig`; migrated into `extra_args`):
`cache_type_k`, `cache_type_v`, `threads`, `threads_batch`, `ubatch_size`,
`batch_size`, `n_cpu_moe`, `flash_attn`, `no_mmap`, `mlock`, `temperature`,
`top_k`, `top_p`, `min_p`, `repeat_penalty`, `reverse_prompt`.

**Kept** (llauncher-acted-on fields): `mmproj_path`, `n_gpu_layers`,
`ctx_size`, `parallel`. **Kept, promoted to the deny-list**: `metrics`,
`slots`.

`--alias`, `-m`/`--model`, `--host`/`--port`, `--api-key` stay denied and
are joined by `--metrics`, `--slots`, `--no-slots`.

## Migration at the door, once (`PARSE-AT-THE-DOOR`)

One rewrite, in `core/config.py::ConfigStore.load`, at the single load
entry point. No dual-parse, no deprecation period, no second pass. For each
dropped field present on a persisted entry:

1. **field would have caused `build_command` to emit its flag, and the flag
   is absent from `extra_args`** → append `<flag> <value>` (bare flag for a
   boolean field) to `extra_args`, drop the field, rewrite.
2. **the flag is already present in `extra_args`** (any spelling
   llama-server accepts for that option, e.g. `-ctk`/`--cache-type-k`,
   `-fa`/`--flash-attn`, `-tb`, `-ub`, `-t`, `-b`, `-ncmoe`) → drop the
   field, the `extra_args` occurrence wins, rewrite. Registering every
   alias is load-bearing, not cosmetic: llama-server resolves a repeated
   option first-wins (#156) and treats long and short spellings as one
   option, so an unregistered alias would leave both `-fa off` and a
   materialized `--flash-attn on` in argv — self-contradictory, and its
   effective value decided by append order.
3. **field present but inert** (would not have caused emission — e.g.
   `None`/`False`/default with no matching flag) → drop the field
   (bookkeeping only), rewrite.

**Defaults are the sharp edge.** `build_command` emitted `-c`,
`--n-gpu-layers`, `--threads-batch`, `--ubatch-size`, `--flash-attn`, and
the slots flag **unconditionally**. For the three dropped fields in that
set (`threads_batch`, `ubatch_size`, `flash_attn`), the migration always
materializes the effective current value — default included — so
post-migration argv is byte-identical to pre-migration argv for every
config on disk (verified by an argv-equivalence golden test). Conditionally
-emitted dropped fields materialize only when they carried a value that
would have caused emission (mirroring `build_command`'s exact prior
conditional).

**Unrecognized shape → quarantine, not tolerance.** The ratified wording,
kept verbatim because the constraint it answers is real: `ConfigStore.load`
rehydrates the registry in one pass, which is exactly why #156 chose
warn-and-continue — one `ValueError` bricks 60+ models. The answer is not a
warning, and it is not failing the registry either. **A config whose shape
does not migrate deterministically is not loaded.** It is recorded as a load
error against that model name and surfaced in the registry's error list;
sibling models still load. The model is unusable until fixed — loud, not
degraded — and the blast radius stays one model. There is no path on which a
config both fails to parse and starts a server.

Quarantined shapes: a key that is neither a current `ModelConfig` field, one
of the 16 dropped fields, nor a legacy field
`ModelConfig.from_dict_unvalidated` already silently drops (ADR-LLNCH-010
`default_port`/`port`/`host`, #235 `np`); a dropped field holding a
non-renderable value (a null on one of the three unconditionally-emitted
fields, whose pre-#477 `int`/`Literal` typing rejected null loudly — writing
the literal token `None` into argv is not a migration); malformed
`extra_args` quoting on an entry that still has dropped fields to place; and
a body `ModelConfig` itself rejects.

Mechanically: `_migrate_config_dict` raises `ModelConfigLoadError` (a
`ValueError`), `ConfigStore.load_with_errors` catches it **per entry**,
returns `(models, errors)`, and logs each quarantine at ERROR;
`ConfigStore.load` is the thin wrapper for callers that only want the
registry. `LauncherState.config_errors` carries the list, and the Model
Registry tab renders one banner per quarantined entry — a model with no row
in the table must not simply vanish from the operator's view. The one-shot
rewrite is **skipped while any entry is quarantined**, because `save`
serializes only the models that loaded and would otherwise delete the very
entry the operator has to hand-fix.

The file-level errors are unchanged and still fail the whole load: an
unreadable (`OSError`) or non-JSON (`json.JSONDecodeError`) `config.json`
has no registry to salvage (#403).

**The read path is never stricter than the write path.** `extra_args` is
tokenized *only* for an entry that still carries pre-#477 fields to place —
never on a migrated entry, and never on one any post-#477 llauncher wrote.
Since ratification point 1 removed all pydantic content validation from
`extra_args`, the UI textarea accepts an unbalanced quote; re-parsing it on
every load, forever, would let the app permanently brick its own registry
with a string it had just accepted. A quoting error surfaces where the ADR
says it does — at launch, in `build_command`, as `MalformedExtraArgsError`.

## Validator posture after

- The `warnings.warn` branch in the former `extra_args_no_managed_flags`
  validator is deleted, along with the load-warns/write-rejects asymmetry
  that existed to support it (and the `_loading_persisted_config_var`
  context flag that implemented it). A config is valid or the load fails
  (migration above) — no persistent nag.
- `MANAGED_NATIVE_FLAG_TO_FIELD`/`MANAGED_NATIVE_FLAGS` are deleted, not
  shrunk. The six kept fields have no `extra_args` collision guard at all
  (pydantic no longer validates `extra_args` content in any way, per the
  ratification directive); `-c`/`--ctx-size`, `-np`/`--parallel` etc.
  colliding with the corresponding field is the operator's problem to
  avoid, same as any other llama-server argv contradiction.
- `DENIED_EXTRA_ARG_FLAGS` moves from `models/config.py` (a pydantic
  field-validator concern) to `core/process.py::build_command` — the
  single, launch-time enforcement point. `docs/ARCHITECTURE.md` rule 5's
  audited-conformance row is repointed at the new home in the same change,
  since it cites this symbol as EMIT-CANONICAL's evidence.
- `build_command` raises one exception family for every `extra_args`
  defect: `ExtraArgsError(ValueError)`, with `DeniedExtraArgError` (an
  owned flag) and `MalformedExtraArgsError` (unparseable quoting —
  reachable precisely *because* pydantic no longer checks it) beneath it.
  `operations/start.py` and `operations/swap.py` catch the **base class**
  alongside their existing launch-failure exceptions, so neither subclass
  can escape as a bare `ValueError` from `shlex.split`.
- The drift-guard in `tests/unit/test_process.py` (every flag
  `build_command` emits is registered against `DENIED_EXTRA_ARG_FLAGS`)
  survives, stricter — the emitted native-flag set is now small and closed
  (`-m`, `--alias`, `--mmproj`, `--n-gpu-layers`, `--host`, `--port`, `-c`,
  `--parallel`, `--metrics`, `--slots`/`--no-slots`).

## Consequences

**Positive:**
- `cache_type_k`/`v`'s `Literal` expressiveness ceiling is gone —
  `extra_args` can hold any value upstream adds, forever.
- One validation surface (`build_command`'s deny-list) instead of two
  (pydantic + `build_command`'s own emission).
- The UI's Advanced Options expander drops from 19 widgets to a name/path/
  mmproj/GPU-layers/ctx-size/parallel/metrics/slots/extra_args surface — a
  single textarea replaces 12 per-flag widgets, removing the UI-space
  schema mirror that was the whole problem one layer down.
- `mcp_server/tools/config.py`'s hand-maintained field list shrinks to
  match; no allow-list drift against a schema this ADR no longer has.

**Negative:**
- Operators lose per-field typed validation (range/enum checks) for the 16
  dropped fields — a typo in `extra_args` surfaces as a llama-server argv
  error at launch, not a pydantic `ValidationError` at config time. Judged
  acceptable: llama-server's own argv parser is the authoritative source of
  truth for its own flags' valid values, and the removed `Literal`
  constraints were already wrong for `cache_type_k`/`v` (#477's root
  cause) — a stale mirror giving false confidence is worse than no mirror.
- Existing tooling/scripts that read the 16 fields from `config.json`
  directly (rather than through `ModelConfig`) must be updated; the
  migration rewrites `config.json` once, on next load, dropping those
  keys.

## Alternatives considered

**A. `cache_type_k`/`v`-only fix (widen the `Literal` or drop just those
two fields).** Rejected per the ratification's Q1: two migrations of the
same persisted artifact under two different rules is the dual-parse shape
wearing a schedule (`PARSE-AT-THE-DOOR` says once). Also more expensive: a
second golden-argv corpus and a second UI edit later, for the same root
cause (mirroring a foreign, evolving schema) other fields already exhibit.

**B. Let llama-server's own defaults take over for unset fields on
migration (no default materialization).** Rejected per the ratification's
Q2: this decision is about where the schema lives, not about changing what
runs on the GPU. A silent perf/behavior change across every persisted model
on upgrade is a different, unratified change riding along; operators can
prune the materialized noise per model afterward if they want llama-server's
defaults instead.

**C. Generic key/value flag editor in the UI instead of a free-text
textarea.** Rejected per the ratification's Q3: a key/value editor
reconstructs the same schema mirror one layer down and re-acquires the same
drift problem against upstream `--help`. A textarea showing flags in the
spelling the operator read from `llama-server --help` is the honest
surface.

## Blast radius

Production edit surface: `llauncher/models/config.py`,
`llauncher/core/process.py`, `llauncher/core/config.py`,
`llauncher/mcp_server/tools/config.py`, `llauncher/ui/tabs/forms.py`,
`llauncher/operations/start.py`, `llauncher/operations/swap.py`. Test
surface: `tests/unit/test_models.py`, `tests/unit/test_process.py`,
`tests/unit/test_models_config_extra_args.py`,
`tests/unit/test_config_migration_026.py` (new),
`tests/unit/mcp/test_config_tools.py`, `tests/ui/test_forms.py`,
`tests/unit/test_ui_rendering.py`, `tests/architecture/`. Docs:
`docs/MCP.md`, `README.md`, `docs/adrs/draft/024-declarative-render-matrix.md`
(amended), `docs/generated/TEST_SUITE_SUMMARY.md` (regenerated).

## Supersession relationships

**Supersedes:** none. **Amends:** ADR-LLNCH-024 (Phase 2 withdrawn; Phases 1/3
re-scoped to the six-field owned column — see the amendment note at the top
of that file). **Amends in spirit:** ADR-LLNCH-007 (repeat-penalty tuning
recommendations stand as operator guidance; the `repeat_penalty` field
behind them does not).
