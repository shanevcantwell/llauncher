# ADR-LLNCH-028: Typed Flags Are The Ones llauncher Reasons About

**Status:** Draft
**Date:** 2026-08-26
**Tracking:** #467 (umbrella tracker — acceptance criteria proposed below).
Folds: #294, #92, #91, #184, #189, #237, #487. Parity obligation: #330.
Depends on: #376 (launch-failure propagation — see the primitive-value clause).
**Amends:** ADR-LLNCH-026 (Accepted, unchanged in its `extra_args`-verbatim
decision; its 16-field partition is narrowed, not reversed). Un-withdraws
ADR-LLNCH-024 Phase 2 under a narrower admission criterion.
**Doctrine:** `PARSE-AT-THE-DOOR`, `ONE-DOOR`, `THIN-CONTRACT`
(`operating-doctrine/ground-physics/GROUND_PHYSICS.md`); project `CLAUDE.md`
(no backcompat shims; UI is a thin client); `docs/ARCHITECTURE.md` rules 4–5.
**Disposition:** `auto:draft` — operator ratifies before implementation.
Decisions A and C are ruled (2026-08-26); B remains, with a proposed default
that becomes the operative clause on silent ratification.

---

## Context

ADR-LLNCH-026 removed 16 llama-server mirror fields from `ModelConfig` and made
`extra_args` a verbatim passthrough. The operator's ask behind #477 was the
`extra_args` half — the ratification comment records the directive verbatim as
*"disable pydantic for `extra_args`"*; the 16-field partition and the
withdrawal of ADR-LLNCH-024 Phase 2 were drafted into the same ratification by
the orchestrator seat and ratified without being separately registered. (The
#477 issue body did pose the wider partition as a scope question, so the wider
scope was on the record — it was not the operator's own stated ask.) That is
why this amendment arrives two days after ratification.

ADR-LLNCH-026's `extra_args`-verbatim decision is the part that was wanted and
stays intact. What ADR-LLNCH-026 did not decide, because nobody asked it, is the
opposite half: a small set of per-entry flags the operator hand-types into
nearly every config should be *controls*. None of them ever were controls —
`cache_type_k/v` were schema fields with no widget — so this is **new surface,
not restoration**. The live registry is the evidence: two of three entries in
`~/.llauncher/config.json` carry `--kv-unified --cont-batching --spec-type
draft-mtp --spec-draft-n-max 2 -ctk q4_0 -ctv q4_0 --no-reasoning-preserve`
hand-typed into a textarea.

A launcher whose only per-model surface is a textarea is a text editor with a
process table.

## Decision

**Mirror nothing the launcher doesn't reason about; type everything it does.**

**Admission criterion.** A llama-server flag is a typed field on `ModelConfig`
**iff** llauncher must *gate*, *constrain*, or *compute from* its value — a
launch-time refusal, a cross-field constraint over another typed field, or a
quantity llauncher publishes on its own contract — **or** the operator
designates it a per-entry control. Recurrence in the operator's habit is not
itself an admission; a designation is, and it is made once, by name, in the
table below. Everything else stays verbatim in `extra_args`, forever.

The second head is honest about what it costs: `-ctk`/`-ctv` are admitted under
it, and llauncher does not gate, constrain, or compute from their values. The
criterion is wider than "the launcher reasons about it" as a result, and saying
so is cheaper than stretching a derivation llauncher does not perform.

**Values are primitives; llauncher never enumerates llama-server's vocabulary.**
A typed field carries its value as the primitive llama-server accepts —
`str`, `int`, `float`, `bool` — and **never** a `Literal`, enum, or
range-constrained type over a value space upstream owns. An invalid value is
rejected by the binary at launch, and that rejection is surfaced at the user
surface. This is #477's lesson generalized: the `Literal` that could not hold
`q4_0` was strictly less expressive than the flag it shadowed, and a stale
mirror giving false confidence is worse than no mirror.

*Enforcement surface:* #376 — the launch path must classify a fast nonzero exit
and propagate llama-server's own diagnosis (`error: invalid argument: …`) to the
UI, CLI, and agent surfaces. Until #376 lands, `operations/start.py` reports
`success=True` for a dead child, so an invalid value fails **silently**. This
clause is **known-fragile** (Part III, prose-only) until then, and #376 is a
hard dependency of this ADR's acceptance criteria, not a nicety.

**The second surface.** Recurring vocabulary that fails the criterion is served
by an `extra_args`-editing affordance — a toggle/chip row that reads and
rewrites the `extra_args` string on the model entry — not by a field. It holds
no state of its own (ADR-LLNCH-025): the string on the entry is the authority,
and the widget is a view of it.

### The admitted set

Spellings verified 2026-08-26 against the operator's own build
(`C:\Users\Shane\github\llama.cpp` @ `25ae3a9b3`, `llama-server --help`).

| Flag (verified spelling) | Admitted because | Field | UI control | Parity surfaces (#330) |
|---|---|---|---|---|
| `-kvu`/`--kv-unified`, `-no-kvu`/`--no-kv-unified` | **Derivation.** The footer's effective per-session window is `ctx_size` under unified KV and `ctx_size / parallel` without it (#91). llauncher publishes that tuple on `GET /footer` (ADR-LLNCH-012, `agent/footer_cache.py`) — the same ground that keeps `ctx_size`/`parallel`. | `kv_unified: bool \| None` (`None` = leave to llama-server) | tri-state select (auto/on/off) | ui, cli, mcp, agent (**read** on `/footer`, #91) |
| `--spec-type` (values incl. `draft-mtp`) | **Gate.** `draft-mtp` with `parallel > 1` is unsupported and is a live runaway hazard on stored configs (#189, #237); `parallel` is already typed, so the constraint is over two typed fields — finite and decidable, not a model-physics oracle. | `spec_type: str \| None` (verbatim, comma-separated) | text input + preset chips | ui, cli, mcp, agent |
| `--spec-draft-model` (`-md`, `--model-draft`) | **Constraint.** A second on-disk artifact reference — the `mmproj_path` class, not a tuning knob: it carries the `model_exists` path validator and a VRAM claim preflight must see. | `spec_draft_model_path: str \| None` | path input | ui, cli, mcp, agent |
| `--spec-draft-n-max` | **Designated**, as a member of the gated spec family. No gate or derivation of its own — see ratification decision **B**. | `spec_draft_n_max: int \| None` | number input | ui, cli, mcp, agent |
| `-ctk`/`--cache-type-k`, `-ctv`/`--cache-type-v` | **Designated** per-entry control (operator ruling, 2026-08-26): KV quant changes per entry and per model, and typing it is what makes it a control. llauncher neither gates nor derives from the value — and the field is `str`, never the `Literal` that caused #477. | `cache_type_k: str \| None`, `cache_type_v: str \| None` | text input + non-binding suggestion list | ui, cli, mcp, agent |

`--reasoning-preserve`/`--no-reasoning-preserve` (the real spelling of the
operator's `--no-reasoning-preserver`) and `--cont-batching`/`--no-cont-batching`
are in the recurring vocabulary, are neither reasoned-about nor designated, and
get chips on the second surface, not fields.

**`--cache-reuse` is not admitted** (operator ruling, 2026-08-26): the
operator's configs run `--kv-unified`, under which `--cache-reuse` has no
effect, so there is nothing for llauncher to gate — it stays verbatim in
`extra_args`.

## Scope vs. the ask

Proposed by `operating-doctrine#76`: state the ask verbatim, then sort this
document's content against it, so scope growth is visible rather than inferred.

**The ask, verbatim.**
- #477 ratification directive: *"disable pydantic for `extra_args`."*
- This session, on the six flags then named (one of which, `--cache-reuse`,
  was declined on 2026-08-26 — see the Decision): they *"may change per entry
  or model"*; and
  *"This is supposed to be a launcher — not a movement into a Streamlit UI to
  just type everything anyway."*
- Decision A ruling: *"the rough thing about adding `-ctk`/`-ctv` is that it
  locked down to an enum, not a scalar or primitive. Leave open and, once
  again, surface syntax errors on failed run attempts."*

| In the ask | Beyond the ask (recorded, not smuggled) |
|---|---|
| ADR-LLNCH-028 existing at all — the named flags becoming controls | The admission criterion as a *general* rule binding future flags |
| Per-`ModelConfig` fields, never `core/settings` host globals | The primitive-value clause generalized past `-ctk`/`-ctv` to every admitted field |
| `str`, not `Literal`, on `-ctk`/`-ctv` | Deny-listing `-c`/`--ctx-size` and `-np`/`--parallel`, which closes #487 |
| Surfacing invalid values at launch (#376 named as the surface) | A second one-shot rewrite of `config.json` within a week of #483's |
| The chip surface — the answer to "not a Streamlit UI to type everything" | `spec_draft_n_max` (decision **B**) — no independent claim |

ADR-LLNCH-026's 16-field partition is **not** re-litigated here; it is cited as
the precedent this ADR narrows. The revert path was assessed and declined by the
operator on 2026-08-26.

## Migration (one-shot at the door)

Extends `core/config.py::_migrate_config_dict`, the single load entry point.
Per admitted flag, per persisted entry:

1. **flag present in `extra_args`** (any registered spelling, long or short) and
   the typed field is unset → parse the value into the field as a primitive,
   **remove the token(s) from `extra_args`**, rewrite.
2. **field set and the flag absent** → nothing (the field is already the
   authority).
3. **field set *and* the flag present** → `ModelConfigLoadError`; the entry is
   quarantined and its siblings load (ADR-LLNCH-026's quarantine machinery,
   unchanged). **Never first-wins.** The published contract and the operator's
   typed intent disagreeing in silence is exactly #487's defect.
4. **flag present, value not coercible to the field's primitive** (e.g. a
   non-integer `--spec-draft-n-max`) → quarantine. Note the narrowness: this rejects
   a *type* error, never a *value* llama-server might accept — `-ctk anything`
   migrates, and the binary judges it (#376).

After migration, every admitted flag joins `DENIED_EXTRA_ARG_FLAGS`
(`core/process.py`) so it cannot be double-specified — a launch-time
`DeniedExtraArgError`, the single enforcement point ADR-LLNCH-026 established.
Under the same clause, `-c`/`--ctx-size` and `-np`/`--parallel` join the
deny-list, which is #487's option 2 and closes it.

**This is not a dual-parse.** Each migration is one deterministic rewrite at the
door, after which no reader parses two shapes of the artifact
(`PARSE-AT-THE-DOOR`). It runs in the opposite direction from #477's — a config
migrated by #483 will have `-ctk q4_0` lifted back out of `extra_args` into a
field, and it lands there intact precisely because the field is `str`. The
invariant that holds is *one shape on disk after each load*, not *one migration
ever*.

## Explicitly excluded

`--threads-batch`, `--ubatch-size`, `--flash-attn`, `--temp`, `--top-k`,
`--top-p`, `--min-p`, `--repeat-penalty` — and with them `--threads`,
`--batch-size`, `--n-cpu-moe`, `--no-mmap`, `--mlock`, `--reverse-prompt`.

llauncher never gates, constrains, or computes from any of them, and none is
designated: no launch refusal turns on their value, no other typed field is
constrained by them, and nothing llauncher publishes is derived from them. The
sampling members are additionally per-request overridable, so a per-entry
default is not even the authoritative value at inference time. They stay
verbatim in `extra_args` under ADR-LLNCH-026, unchanged. ADR-LLNCH-007's
repeat-penalty guidance stays operator guidance, as ADR-LLNCH-026 left it.

## Relation to ADR-LLNCH-024 and ADR-LLNCH-026

**ADR-LLNCH-026** stays **Accepted**. Its decision — `ModelConfig` is not a
mirror of llama-server's argument schema, `extra_args` is verbatim with no
pydantic content validation — is unchanged and is the load-bearing half. *Does
not mirror* was never *does not type*: the two collapse into one thing only if a
typed field re-declares upstream's value vocabulary, which the primitive-value
clause forbids. What changes is the size of the owned column: 6 kept fields → up
to 12, each admitted by the criterion above rather than by the
"llauncher-acted-on" judgement call the partition table used.

**ADR-LLNCH-024** Phase 2 is **un-withdrawn, re-scoped**: it promotes the five
flags in this ADR's table and no others, and it inherits this ADR's migration,
primitive-value, and deny-list clauses. Phase 1/3 dispositions from
ADR-LLNCH-026's amendment stand; the Phase 1 column grows from ~8 rows to ~14.
ADR-LLNCH-024's non-goal (cells encode no semantic compatibility knowledge) is
**kept**: the MTP × `parallel` gate lives in the launch path, not in a matrix
cell. Its `RenderSpec.transform` must not become a
value-validation hook — that would re-acquire the enumeration this ADR forbids.

## Consequences

**Positive**
- The recurring vocabulary becomes clickable across all four surfaces; the
  launcher stops being a textarea for the flags the operator changes per entry.
- #91's footer math becomes computable; #189/#237's MTP hazard becomes a
  refusal instead of documentation; #487's silent drop becomes a load-time
  quarantine.
- The owned column now has a criterion, so the next flag's admission is decided
  by a test and a designation rather than by a judgement call.

**Negative**
- A second migration of `config.json` within a week of #483's. Mitigated by
  determinism and the quarantine path; the operator sees churn, not loss.
- Every admitted flag joins the deny-list, so an existing `extra_args` string
  carrying one becomes a launch-time refusal after migration. This is fail-loud by design and
  the migration removes the tokens it lifts, so only a hand-edit after migration
  can hit it.
- **The primitive-value clause trades config-time rejection for launch-time
  rejection, and llauncher cannot yet show a launch-time rejection** (#376).
  Until #376 lands, a typo in `cache_type_k` produces a dead server reported as
  started. This is the sharpest known-fragile edge in the ADR.

## Enforcement surface

`tests/unit/test_process.py::TestBuildCommandDenyList`, extended to a
**bidirectional** guard:

1. Every native flag `build_command` emits is in `DENIED_EXTRA_ARG_FLAGS`
   (existing drift-guard). `_UNGUARDED_OWNED_FLAGS` is **emptied** — its four
   members (`--mmproj`, `--n-gpu-layers`, `-c`, `--parallel`) are deny-listed by
   this ADR's migration clause.
2. Every flag in `DENIED_EXTRA_ARG_FLAGS` either maps to a `ModelConfig` field
   or appears in a `LAUNCHER_OWNED_NO_FIELD` table carrying a one-line reason
   (`--alias`, `--host`, `--port`, `--api-key`, `--metrics`, `--slots`,
   `--no-slots`). A new deny-list entry with neither fails the test.
3. **No-enumeration guard:** a test over `ModelConfig.model_fields` asserting
   that no field rendered into argv is annotated `Literal`/`Enum` or carries a
   value-range constraint. This is the primitive-value clause made structural —
   the half that does not depend on #376.
4. A migration test per admitted flag covering the four branches, including the
   conflict quarantine (branch 3).

Parity (#330) is pinned by the existing dispatch-seam parity pattern extended to
config-field writability: a field writable in the UI form and not in the MCP
`update_model_config` schema fails.

## Follow-up

**#467 becomes the tracker.** Proposed acceptance criteria:

- [ ] ADR-LLNCH-028 ratified; decision B resolved on the issue.
- [ ] Each admitted flag has a typed `ModelConfig` field of primitive type
      (no `Literal`/enum/range), is on `DENIED_EXTRA_ARG_FLAGS`, and is
      writable through ui / cli / mcp / agent.
- [ ] `_migrate_config_dict` lifts each admitted flag out of `extra_args`;
      field-vs-`extra_args` conflict quarantines the entry (never first-wins).
- [ ] The no-enumeration guard is green.
- [ ] `kv_unified` is served on `GET /footer` and the effective per-session
      window is correct in both modes (#91).
- [ ] Launch is refused for `spec_type` containing `draft-mtp` with
      `parallel > 1` (#189, #237).
- [ ] The bidirectional deny-list guard is green and `_UNGUARDED_OWNED_FLAGS`
      is empty (#487 closed).
- [ ] `--reasoning-preserve` / `--cont-batching` chips edit `extra_args` and add
      no field.
- [ ] **#376 landed**, or the known-fragile note is carried forward explicitly
      on #467 with the residual risk named.

**Closes under it:** #92, #91, #294 (re-framed to this scope), #487.

**#184** (hazard: `--cache-reuse` on hybrid-recurrent backends) closes as
documentation — the hazard note lives in the `extra_args` help text and the
README, not a gate. **#237**'s MTP `parallel > 1` half stays live under the
`--spec-type` gate above; its `--cache-reuse` half closes with #184. #189
stays `user:gate` until its live config is corrected.

## Ratification decisions

**Decision A — `-ctk`/`-ctv` — RULED 2026-08-26.** Admitted as typed
`str | None` fields, never a `Literal`, and generalized into the
primitive-value clause above. The preflight KV-footprint obligation floated in
the first draft is **withdrawn** — it was a derivation llauncher does not
perform; the admission stands on per-entry designation instead.

**Decision C — `--cache-reuse` — RULED 2026-08-26.** Not admitted; no
`kv_backend` field is created. Recorded in the Decision above.

The one below is open. Its proposed default becomes operative if ratified
without comment.

**B. `--spec-draft-n-max`.** Pure tuning; nothing gates or derives from it.
*Proposed default:* admit it as a member of the gated spec family, because a
spec-decoding control group split across a field and a textarea is worse than
either. *Alternative:* strict criterion — it stays in `extra_args`, and the UI
group shows a chip beside the two admitted spec fields.

## Supersession relationships

**Supersedes:** none. **Amends:** ADR-LLNCH-026 (owned-column scope only),
ADR-LLNCH-024 (Phase 2 reinstated, re-scoped). **Superseded by:** TBD.
