# ADR-017: Inter-Context Agent Communication Protocol (Message / Dossier)

**Status:** Draft
**Date:** 2026-05-31
**Relationship to other ADRs:** ADR-001 (pi-coding-agent as consumer harness), ADR-009 (symmetric hub/spoke topology — same coordination shape across nodes), ADR-012 (footer-context endpoint — a concrete inter-context payload), ADR-016 (canonical self-swap)
**Supersedes:** None

> **Sub-filing note.** This ADR is intended to graduate into a larger
> **"LAS → harness adaptation"** story. That story lives outside this
> repository (the `pi-las-integration` ecosystem); it is not reachable or
> editable from within `llauncher`. Until that parent exists as a linkable
> artifact, this ADR stands alone here and cross-links the harness-facing
> ADRs above. The cross-repo link should be added from the LAS side.

## Context

Orchestration in this ecosystem is increasingly *decompose-and-delegate*: a
top-level context dispatches work to subagents whose contexts are **ephemeral**
and do not share memory with the dispatcher or with each other. Two problems
follow that are not addressed by treating subagent calls as ordinary tool calls:

1. **Context salience.** The orchestrator window should be a *story of
   decisions*, not a dumping ground for execution residue (search output, file
   dumps, git plumbing). Lossy auto-compaction of a spent window degrades
   embedding quality. The discipline that preserves salience is to push noise
   into ephemeral subagent contexts and receive back only a compressed,
   contract-shaped result.

2. **State survival across ephemeral contexts.** When a delegated exchange
   becomes multi-turn — e.g. a subagent must pause and bounce a decision
   upstream — the state of that exchange cannot live *in* the subagent, because
   the subagent's context may be gone before the exchange resumes.

This was observed concretely this cycle: a git subagent dispatched to commit a
file hit a `.gitignore` precondition, returned a "decision needed upstream"
frame (correct behavior), but because no durable exchange state existed and the
live-messaging channel was unavailable, the exchange had to be **cold-restarted**
with the situation re-fed by hand. The re-feed *was* the missing durable state.

What we kept re-deriving, term for term, is the LangGraph model — checkpointed
state, `thread_id`, `interrupt()` / `Command(resume=...)`, channel reducers.
This ADR adopts that model deliberately and names the two places we go *past*
vanilla LangGraph (below), because those two deltas are the whole reason a
protocol layer is needed rather than a single compiled graph.

## Decision

Define two dual artifacts: the **Message** (transient inter-context frame) and
the **Dossier** (durable backing store). A Message is what travels on an edge;
the Dossier is the substrate its pointers point into. Current protocol state is
a *fold over the Dossier*.

### 1. Message — the transient frame

```yaml
Message:                          # ≈ a node's emitted update / Command(goto=, update=)
  protocol_version, correlation_id, turn, from, to, in_reply_to, kind
  #                  └─ ≈ LangGraph thread_id

  contract:                       # request side; null on responses
    problem:      str
    facts:        [str | Pointer] # pointers preferred over inlined content
    return_shape: Schema          # the declared payload shape — what makes a return an *answer*
    guardrails:   [str]

  # ── response side: status SPLIT into node (disposition) + edge-cause (reason) ──
  disposition:                    # CLOSED enum — the dependability category; receiver dispatches on this
    OK        # correct service                          -> consume
    BLOCKED   # FAULT (exogenous): suspend, resumable     -> fix upstream, resume   (≈ interrupt)
    HALTED    # ERROR (endogenous): detected & contained  -> accept; re-scope       (valid completion)
    FAILED    # FAILURE: service deviated / infra wall     -> escalate (a FAULT for the parent)
  reason: <Cause> | null          # OPEN taxonomy — the specific cause within the category
    #  faults:   MISSING_PRECONDITION | AMBIGUOUS | CONTRACT_MISMATCH | SCOPE_CREEP
    #  errors:   STAGNATION | HALLUCINATION_RISK
    #  failures: INFRA | SYNTAX | <uncontained fault>

  payload: { fields, pointers[] } | null   # shaped to return_shape; pointer-based
  extended_args: { state_machine: {…} }    # the nursery (emergent delta — see §3)
```

### 2. Dossier — the durable backing store

```yaml
Dossier:                          # ≈ LangGraph checkpointed State (channels);  dossier_id ≈ thread_id
  dossier_id, scope, actors, span
  lifecycle: OPEN | RESUMABLE | CLOSED        # exchange-level (distinct from per-frame disposition)

  # per-field REDUCERS make the append-vs-snapshot choice explicit (≈ channel reducers):
  decisions:      «add»          # append-only — the audit spine (irreversible)
  pointers:       «add»          # append-only — {path, kind, sha?}
  contracts:      «add»          # append-only
  state_machine:  «last-value»   # overwrite  — the resumable head (the snapshot)
  open_items:     «last-value»   # overwrite

  interrupt:                     # present iff lifecycle == RESUMABLE  (≈ the interrupt payload)
    decision_points: [ { id, question, options?, recommended? } ]
    resume_with:     <schema>    # what Command(resume=…) must supply
```

The "log the irreversible, snapshot the live" choice is expressed as a
per-field reducer (`add` vs `last-value`), the LangGraph-native way to say it.

### 3. Disposition spine: the fault / error / failure taxonomy

`disposition` is grounded in the classical dependability chain
(*fault → error → failure*: cause → bad state → observable deviation):

| disposition | dependability term | driven by | nature | receiver does |
|---|---|---|---|---|
| `OK`      | correct service              | —                          | —          | consume |
| `BLOCKED` | (no error) suspension        | a **FAULT**                | exogenous  | fix upstream → resume |
| `HALTED`  | error detected, failure averted | an **ERROR**            | endogenous | accept containment → re-scope |
| `FAILED`  | **failure**                  | uncontained fault / infra wall | —      | escalate (system-critical) |

The FAULT↔ERROR line is load-bearing: it is exactly what distinguishes
`BLOCKED` (someone else's defect — resumable once they fix it) from `HALTED`
(your own state diverging — you stop to contain it before it propagates).
Honest-failure-exit is therefore **`HALTED`, not `FAILED`** — error
containment is a *valid completion*, not a service deviation. Only an
uncontained fault or an infrastructure/syntax wall is a true `FAILED`.

**Cross-boundary recursion:** in dependability theory, the *failure* of a
component is a *fault* for the enclosing system. A child subagent returning
`FAILED` is thus a FAULT injected into the orchestrator's state — which is why
the correct parent response is to record and escalate, not absorb.

### 4. Closed core, open nursery, promotion

- `disposition` and the Dossier lifecycle are **closed** enums — a procedural
  layer can validate them. This is what retires the "trust-the-prose" risk of a
  free-form summary.
- `reason` and `extended_args` are **open** — emergent state machines define
  their own state without the core knowing in advance.
- Features **graduate**: an emergent machine lives in `extended_args`
  (open, unvalidated) until it recurs/stabilizes, then is promoted into the
  closed core (validated). The protocol grows by promotion, mirroring the
  signal-driven tier promotion already used for subagent selection.

### 5. Relationship to LangGraph (prior art + the two deltas)

| this protocol | LangGraph primitive |
|---|---|
| Dossier | checkpointed State (channels) + checkpoint |
| `correlation_id` | `thread_id` |
| Message | a node update / `Command(goto=, update=)` |
| `disposition: BLOCKED` → resume | `interrupt()` / `Command(resume=…)` |
| `disposition: OK`/`FAILED` | conditional-edge routing / route to `END` |
| append-log vs snapshot | channel reducers (`add` vs last-value) |

**Delta 1 — cross-*context*, not cross-*node*.** LangGraph nodes share one
process and one in-memory State. Our actors are separate ephemeral contexts
with no shared memory, so the State object must be the durable Dossier and the
edges must be a real wire protocol (Messages). The checkpoint is the *only*
shared medium.

**Delta 2 — emergent, not compiled.** `StateGraph(...).compile()` declares the
graph up front. Ours accretes at runtime; `extended_args` is where a state
machine lives *before it has been declared*. This is the part with no LangGraph
primitive, and the reason for the open nursery + promotion lifecycle.

## Consequences

- **Salience preserved.** The orchestrator holds contract + compressed result
  (a pointer), never the subagent's execution residue.
- **State survives context death.** Because the state machine lives in the
  Dossier (durable), a fresh context rehydrates from `state_machine.resume`
  instead of hand-re-feeding. The cold-restart wound observed this cycle does
  not recur once a checkpointer/Dossier exists.
- **Signals are validatable.** The closed disposition spine lets a procedural
  layer check frames; non-`OK` dispositions are typed, legible, and
  routable rather than buried in prose.
- **Honest failure is correctly categorized.** `HALTED` ≠ `FAILED` aligns the
  protocol with the operating principle that honest failure is a valid
  completion, *not* a failure — and with the rule that infra/syntax failure is
  a system-critical `FAILED` to escalate.

## Deferred Work

- **Doctrine vs. enforced schema.** Whether this becomes prose guidance in the
  agent constitution or an actual message schema enforced by the harness
  extension is unresolved. Procedural enforcement is the stronger form.
- **Validation policy for `extended_args`.** Free / self-describing /
  registry-by-`fsm_id`. Registry is preferred — it *is* the promotion mechanism.
- **Promotion trigger.** What recurrence count moves a frame from nursery to
  core, and who adjudicates it.
- **Exchange-dossier location & granularity.** Session dossiers live in
  `docs/handoffs/`. Fine-grained per-exchange dossiers (born only when an
  exchange goes multi-turn) need a home — possibly lighter/ephemeral.
- **Closure semantics.** What flips a dossier `CLOSED` (likely: its state has a
  durable home elsewhere — a commit, an ADR).
- **LAS → harness sub-filing.** The parent story is external (`pi-las-integration`)
  and out of this repo's writable scope; the cross-repo link must be added there.
