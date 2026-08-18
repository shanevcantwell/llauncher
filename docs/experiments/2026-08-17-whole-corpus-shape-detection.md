# Experiment: whole-corpus shape detection by unaided forward pass

**Date:** 2026-08-17
**Repo under test:** `llauncher` @ `7642e5d` (main, after PR #385)
**Status:** predictions committed before observation

---

## Question

Can a model, given the entire source corpus resident in context and *no tools*, detect
structural wrongness ("wrong-shaped-ness") that targeted search does not surface?

## Why now

This session produced a control set by the opposing method. Working from an orient brief,
five targeted subagent dispatches (PR sweep → review → call-graph map → architecture
reconciliation) surfaced a structural defect:

> `state.py::stop_server` and `operations/stop.py::stop` are two implementations of one
> verb, with divergent ground truth (in-memory scan vs. lockfile), divergent durability
> (in-memory audit vs. durable audit log), and divergent policy — only the `state` path
> enforces `ChangeRules.validate_stop`. Since all four live surfaces route through
> `operations.stop`, **that policy is unenforced in production.** The `state` path is
> reachable only from `_start_with_eviction_impl`, itself callerless.

That gives a known answer to compare against, rather than judging output on plausibility.

## Method

Deliberately pre-agentic. The corpus (64 files, 14,349 lines, ~138K tokens) is
concatenated and loaded **directly into context**. The model gets no Read, no Grep, no
subagents — search must not do the finding, because the variable under test is what
attention alone resolves.

Hierarchy is expressed as **successive prompts over the same resident corpus**, not as
agents summarizing to each other. Stateless emitter, re-attended from a new angle each
pass; no lossy handoff between levels.

- **Pass 1 (altitude):** unprompted — "what is wrong-shaped here?" No defect class named,
  no region pointed at. This is the pass the experiment actually tests.
- **Pass 2 (descent):** sharpen survivors into falsifiable claims with coordinates.
- **Pass 3 (refutation):** fresh context, no access to pass-1/2 reasoning, told to attack.
- **Pass 4 (reconciliation):** survivors vs. what `docs/ARCHITECTURE.md` claims is true.

## Predictions (H0 and alternatives, committed pre-observation)

**P1 — the headline.** Pass 1 names the duplicate `stop` verb *unprompted*.
Confidence: ~60%. This is the primary success criterion.

**P2.** Pass 1 does NOT name the unenforced `ChangeRules.validate_stop` asymmetry.
Confidence: ~75%. Reasoning: the asymmetry lives inside function bodies, below the
altitude at which structural shape is legible. Expected to arrive only on descent, if at
all. *If P1 holds and P2 is falsified — i.e. it finds the policy gap too — that is the
strongest possible result and the one that would most change practice.*

**P3.** Pass 1 also names the dead `_start_with_eviction_impl` subtree.
Confidence: ~70%. A ~280-line callerless region is the most structurally visible defect in
the corpus.

**P4 — the cost side.** Precision is poor in a specific direction: confident structural
claims with drifted or invented coordinates, and asserted call relationships that *should*
exist given the inferred shape but do not. Predicted survival rate through pass 3:
**30–50%** of pass-1 candidates.

**P5.** Pass 1 surfaces at least one true structural defect that the five targeted
dispatches did NOT find. Confidence: ~50%. This is the value-add question — recall beyond
the control — and is independent of P1.

**Null result worth banking.** If pass-1 findings survive refutation at <20%, or it takes
the full descent to rediscover what targeted search already found, the conclusion is
"unaided global context does not beat decomposition for structural defects," and that
closes a path.

## Threats to validity

- **Contamination:** the corpus contains `docs/`-adjacent strings and in-code comments
  naming the #57/#332 migration. If pass 1 finds the duplicate verb by *reading a comment
  that says so* rather than by seeing the shape, that is not the capability under test.
  Recorded as a discriminator: check whether findings cite comments or structure.
- **Control asymmetry:** the targeted dispatches ran against the same corpus but were
  steered by an orient brief and operator questions. Not a clean A/B; the control had
  human framing the experimental arm lacks.
- **Single trial.** One run, one model. No claim about variance.

## Result

Pass 1 returned 10 findings (~265K subagent tokens, one forward pass over the corpus, no
tools after load). Four were sent to adversarial refutation — fresh contexts, no access to
pass-1 reasoning, each instructed to default to refuted and pointed at the single kill-shot
most likely to end the claim.

### Predictions scored

| | prediction | conf. | outcome |
|---|---|---|---|
| **P1** | names the duplicate `stop` verb unprompted | 60% | **HIT, partial credit** — found the dead legacy stack, but marked the "legacy/superseded" framing as DOCUMENTED (read from comments). The STRUCTURAL half — that it is now *entirely* bypassed by the live path — is genuinely seen. |
| **P2** | does NOT find the policy-enforcement asymmetry | 75% | **FALSIFIED — the strongest result.** It found it, and landed somewhere sharper than the control: `ChangeRules` is consulted on only one path, so blacklist/whitelist policy is bypassed everywhere else. My altitude reasoning ("lives inside function bodies, below structural resolution") was simply wrong. |
| **P3** | names the dead `_start_with_eviction_impl` subtree | 70% | **HIT.** |
| **P4** | 30–50% of candidates survive refutation | — | **TOO PESSIMISTIC.** 4/4 survived in some form; 2/4 fully intact. Failure mode also mis-predicted: no invented coordinates (it honored the instruction to mark confidence rather than fabricate precision). Errors ran toward *understating* the defect, not confabulating one. |
| **P5** | finds true defects the control missed | 50% | **HIT, strongly.** All four refuted findings were outside what five targeted dispatches surfaced. |

### Refutation outcomes

- **MCP `stream_logs` ignores its own `lines` parameter — CONFIRMED.** Positional call binds
  `lines` to `model_name`; a guard that would raise is skipped whenever the port resolves,
  absorbing it silently. *Refutation found more than the claim*: `test_custom_lines_passed_through`
  mocks `stream_logs` and asserts the **second positional argument**, i.e. it certifies the bug's
  own calling convention and would pass both before and after a fix.
- **stop/swap kill by argv scan — CONFIRMED.** Both hold the authoritative lockfile pid, thread
  it through every audit entry, then kill via first-argv-match. Lockfile removed unconditionally;
  audit records a pid nothing verified; kill primitives return only `bool`, so mismatch is
  undetectable by construction. The aimed kill-shot (two processes can't share a port) failed:
  ADR-015 §Decision-2 defines "lockfile points at a different live pid on same port" as an
  anticipated orphan state.
- **Policy unenforced — PARTIALLY-CONFIRMED.** Shape real, mechanism wrong. The claimed
  control flow (UI's delegated branch unchecked) is false — the UI checks once before the fork.
  The true gap is *larger*: CLI, MCP, and direct agent-HTTP bypass `ChangeRules`
  unconditionally, every time.
- **Scan cache never invalidated — PARTIALLY-CONFIRMED.** All 9 invalidator call sites sit in
  the bypassed `state.py` stack; no live `operations/` path invalidates; no TTL bypass exists.
  "Dead code" overstated (tests and a CLI path construct `LauncherState`). Observability splits:
  start path masked by model-load latency exceeding the 3s TTL; **stop path genuinely stale** —
  `/stop` returns 202 immediately by design (#140), the rerun lands inside the window, and the
  server renders as running for up to 3s after the click.

### What this establishes

Unaided whole-corpus attention found four real defects that targeted decomposition did not
reach, at a survival rate well above prediction. It is a **finder**, not an oracle: two of four
carried wrong mechanism detail while being right about the shape — which is exactly why the
refutation stage is load-bearing and why findings must not ship unverified.

The `STRUCTURAL` vs `DOCUMENTED` discriminator earned its place: without it, P1's hit would
have been unfalsifiable, since the corpus contains comments naming the migration.

### Unplanned observation — a recurring defect family

Two independent methods found the same pathology on the same day: **a test that mocks its
subject and then asserts the mock's shape**, converting absent verification into a green check.
- PR #183's A2 repro would have kept passing after the defect it pinned was fixed.
- `test_custom_lines_passed_through` asserts the buggy positional convention.

Neither was found by looking for it. Worth a dedicated sweep; not tested here.

### Next arm (designed, not run)

Same resident corpus, probes phrased to point in deliberately different directions —
review-framed, shape-framed, archaeology-framed ("what here is a fossil of a decision since
reversed?"), adversarial-framed. If findings overlap substantially, framing is decoration. If
they diverge systematically, prompt-writing for this class is instrument-aiming, and the
phrasing that found P2 is a reusable asset rather than an accident.

**Caveat on interpretation:** the "probe direction" framing was constructed *after* seeing this
result and currently has one datum. It is a hypothesis to be tested behaviorally, not an
explanation earned.
