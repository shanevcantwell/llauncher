# Handoff — qwen3.6 record-skipping investigation (cross-repo) + llauncher config hygiene

**Date:** 2026-06-25 (US/Mountain) · **Author:** orchestrator session · **Status:** investigation complete; MTP & cache-reuse exonerated; #54 redirected to harness-side; build-arm test pending.

## TL;DR

A "research qwen3.6 verbatim tool-call looping" session became four independent threads (none interdependent) + a serving-config A/B. **Headline: the record-skipping is a deterministic qwen3.6 model/context behavior — MTP and `--cache-reuse` are both empirically exonerated.** Landed the missing **#184 cache-reuse fix on the Q4 config**; filed 5 issues; pulled llama.cpp to b9789 (no #22746 fix upstream yet). The qwen3.6 fix surface moved to **harness-side** (`pi-mcp-adapter` / reasoning_content handling) and/or the launcher watchdog — **not** a serving-config knob.

## What changed

**llauncher runtime (live, this host):**
- `Qwen3.6-27B-UD-Q4_K_XL` on `:8081` — **`--cache-reuse 256` stripped** (the #184 fix the Q4 variant had missed; only Q6 was done). Current `extra_args`: ` --cont-batching --kv-unified --spec-type draft-mtp --spec-draft-n-max 2`. MTP **on**, `parallel 3`. Applied via `update_model_config` (persisted); server restarted + smoke-verified healthy.
- `:8082` embeddinggemma resident, untouched.

**Source (not deployed):** `~/github/llama.cpp` pulled `b9442+144 → b9789+7` (210 commits, clean ff). **Running `:8081` binary is unchanged** — source pull ≠ deploy.

## The serving-config A/B (the load-bearing result)

Method: seed-pinned (`seed:42`) deterministic replay of a **reconstructed known-skip request** (`/tmp/qwen-skip-repro.json`, ~156k-token context ending in 3× `exit code 2` on the same `bash` probe) sent **directly to `:8081`**, one knob varied at a time, `parallel` held at 3. Compared on `args` + `reasoning_content` (tool_call `id` is randomized — excluded).

| Run | Config | Output |
|---|---|---|
| O_B | cache-reuse on, MTP on | skip (deterministic) |
| O1 | cache-reuse **off**, MTP on | byte-identical to O_B |
| O2 | cache-reuse off, MTP **off** | byte-identical to O1 |

- **MTP exonerated** — byte-identical on/off at fixed seed ⇒ genuinely lossless/verification-gated; does not alter completions.
- **cache-reuse exonerated** for the skip (O1==O_B; #184's detonation is the *cross-turn* rewind path, dormant on a single cold request — prefill ran clean ~450–500 t/s).
- **Skip = model/context behavior**: qwen3.6 re-emits the same probe ignoring repeated `exit code 2`. Seed/sampling won't fix it (behavior persists across seeds; only bytes vary).

## Open items (all independent — tracked in GH Issues, not here)

| Issue | Thread | Status |
|---|---|---|
| harness-tools#54 | qwen3.6 record-skipping (**the original problem**) | redirected to harness-side fix + watchdog; build-arm pending |
| harness-tools#55 | extension-load fatality → ADR | filed |
| harness-tools#56 | launcher pre-flight + loop watchdog | filed (reinforced: model won't self-correct) |
| harness-tools#57 | pi-jail → harness-tools/pi consolidation | filed (git-provenance is the hard part) |
| llauncher#189 | MTP `parallel=3` vs `-np 1` + seed/anti-loop | filed; **parallel arm still UNTESTED** |
| llauncher#184 | `--cache-reuse` hybrid-recurrent hazard | Q4 fix landed; **structural promotion still open**; upstream #22746 unfixed in b9789 |

## Next step — the build arm

The only un-run experiment for the "10× worse since last build" claim: **rebuild llama.cpp at b9789, point llauncher at it, replay `/tmp/qwen-skip-repro.json`.** If the skip changes → an upstream fix resolves #54. This is an **infra op** (compile + binary swap) needing operator coordination. Operator's read: the PEG tool-call hardening cluster is likely a red herring (the parser faithfully emits well-formed-but-redundant calls); Eagle3 (`b14e3fb90`, qwen3.6 spec support) is a *lossless* MTP-alternative — a perf/stability option for #189, not a skip fix.

## Gotchas / environment

- **`update_model_config` does NOT expose `parallel`** — only ctx/extra_args/flash_attn/threads/gpu_layers/mmap. Testing `parallel=1` needs a direct config-store edit (off-MCP). This is why the parallel arm is deferred to #189.
- `tool_call.id` is **randomized per response** — exclude it from any determinism/byte comparison.
- Keep `--cache-reuse` **off** for all hybrid-recurrent (Qwen3.6) configs until #184's arch-gating lands; upstream #22746 has no fix as of b9789.

## Re-entry pointers

- `harness-tools#54` comments — the cold-start context + the A/B verdict (full method/table).
- `/tmp/qwen-skip-repro.json` (+ `qwen-skip-seed42.json`) — reusable seed-pinned repro for the build arm (ephemeral /tmp; regenerate from session `019eee62` if cleared).
- `llauncher#184` / `#189` — the two llauncher serving-config threads.
