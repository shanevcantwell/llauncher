# 2026-08-27 — the UI-stall quantum: psutil walk cost on the Windows seat

Cross-refs: run-ledger #520 · fix issue #521 · #309 #392 #464 #466 #468 #497 #498

## H0

Every observed Streamlit stall on the Windows seat is an integer multiple
of a single fixed cost: one `psutil.process_iter([..., "cmdline"])` walk
of the live process table, dominated by the per-process `cmdline()` call
(a handle-open + PEB read on Windows with no batch path). If true, stall
durations cluster near N × ~3.1s rather than varying continuously with
process count, page complexity, or model size.

## Prediction

- The "quantum" is set by the walk cost measured in isolation
  (microbenchmark below), not by anything Streamlit- or model-specific.
- Every measured UI stall on this seat decomposes as (a small number of)
  × (the quantum), where the multiplier is the count of walk-triggering
  calls on that code path (`find_all_llama_servers`, `discover_all`,
  `is_port_in_use` via `can_start()`), not a continuous function of
  system load.
- Replacing the walk's per-process `cmdline()` cost with a name-first
  filter (#521) collapses the quantum from ~3.1s to low milliseconds,
  and every stall that was N × quantum drops to N × (new, near-zero
  quantum) — i.e., the stalls disappear, not just shrink proportionally.

## Before-table (from #520's context)

Measured 2026-08-27 with a headless Playwright driver, T0 before browser
launch, on the Windows seat:

| stall | duration | multiple of ~3.1s |
|---|---|---|
| first full render | 50 s | 16× |
| port-blur → Start-enabled | 43 s | 14× |
| server-ready → 🟢 | 32 s | 10× |

## Benchmark table (from #521, psutil 7.2.1, ~315 processes)

| walk | median |
|---|---|
| `process_iter(["pid","name"])` | 1.1 ms |
| `process_iter(["pid","name","cmdline"])` — old `find_all_llama_servers` | 3,083 ms |
| name-filter first, `cmdline()` only on matches | 1.5 ms |
| `is_port_in_use(8090)` — old, uncached | 6,170 ms per call |
| `psutil.net_connections(kind="tcp")` | 0.3 ms |

Reproduced via `docs/experiments/bench_psutil_walk.py` on this seat
post-fix (317 processes, 1 name match):

| walk | median |
|---|---|
| `process_iter(["pid","name"])` | 1.1 ms |
| `process_iter(["pid","name","cmdline"])` (old shape, still measured for comparison) | 3,162 ms |
| name-filter-first (new shape) | 2.4 ms |
| `net_connections(kind="tcp")` | 0.8 ms |
| `find_all_llama_servers()` (real function, post-fix) | 0.0–12.8 ms |
| `is_port_in_use(8090)` (real function, post-fix) | 0.7–1.1 ms |

Both figures land comfortably under the #521 acceptance criterion of
< 50 ms for each.

Post-fix `is_port_in_use` answers a narrower question than the walk it
replaced: **a socket in the LISTEN state** holds the port. Non-LISTEN
states (TIME_WAIT, CLOSE_WAIT, an outbound socket whose local port
collides) are not occupancy — a fresh server binds straight over them,
and counting them is exactly the phantom "port occupied" #518 chases.
When the socket table cannot be read at all (`psutil.AccessDenied` —
macOS/BSD as non-root, hardened Linux), the function falls back to a
bind probe on the single port in question (no `SO_REUSEADDR`, so it
fails exactly where a real listener would).

## After-table (headless driver, same T0→T10b protocol as the before-table)

Measured 2026-08-27 on the Windows seat: second Streamlit instance on
:8502 launched from the fix worktree, Ornith started on :8091, same
headless Playwright driver and T0 (before browser launch) as the
before-table. Artifacts under `%TEMP%\ui_drive_after\`.

| milestone | before (Δ from T0 / Δ prev) | after (Δ from T0 / Δ prev) |
|---|---|---|
| T3 title visible | 1.3 s | 1.2 s |
| T4 Models tab clickable | +51.3 s / **+50.0 s** | +2.9 s / **+1.7 s** |
| T6 port typed + blurred | +51.5 s | +3.2 s |
| T7 Start toggle enabled | +94.9 s / **+43.4 s** | +3.8 s / **+0.59 s** |
| T8 clicked | +95.0 s | +4.0 s |
| T10b /v1/models 200 | +155.8 s / +60.7 s after click | +23.1 s / +19.2 s after click |
| T10 UI shows running | ≈+188 s / **≈+32 s after server ready** | +21.7 s / **1.5 s before server ready** |

Post-start interaction latencies on :8502 (all sub-50 ms, i.e. below the
threshold at which a click reads as instant): Dashboard tab 45.9 ms,
back to Models 42.7 ms, expand Details 33.2 ms, collapse 36.8 ms,
port-edit blur → toggle enabled on the Qwen3.8 card 8.8 ms.

### Reading the table

1. **Both gated criteria hold.** Port-blur → Start-enabled is +0.59 s,
   under the < 3.1 s gate (it was +43.4 s — fourteen quanta). First full
   render is +2.9 s, under the < 10 s gate (it was +51.3 s).
2. **The 32 s UI-lag row was walks too — it inverted rather than
   shrank.** "UI shows running" now lands 1.5 s *before* `/v1/models`
   answers 200, because the optimistic toast fires ahead of the glyph
   flip; the lag did not become small, it changed sign. A detection
   nuance in the driver, not a claim that the UI is precognitive.
3. **The 60.7 s → 19.2 s model-load delta is not this fix.** That is the
   OS file cache — the second load of the same GGUF on the same day.
   #521 does not touch the load path; nobody should credit it here.
4. **Measurement hazard, for the record.** With llauncher pip-installed
   editable against the shared checkout, `PYTHONPATH=<worktree>` is
   defeated when the cwd *is* the shared checkout: `''` precedes
   `PYTHONPATH` on `sys.path`, so the checkout's copy wins and the
   "after" run silently measures the "before" code. Invoke from a
   neutral cwd (or from the worktree itself) and verify
   `llauncher.__file__` before trusting any worktree measurement.
5. **H0 verdict: confirmed.** The quantum was the Windows `cmdline()`
   envelope cost — a per-process handle-open + PEB read with no batch
   path — and every stall decomposed as N × that cost. The fix stayed in
   envelope space: no trust-model change, OS still ground truth
   (ADR-LLNCH-008 rule 3), `state.py` untouched.

Cross-refs: #520 (run ledger) · #521 (fix) · #522 (PR).
