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

## After-table (headless driver, same T0→T10b protocol as the before-table)

**TODO** — a separate measurement step (second Streamlit instance on
:8502 from the fix branch, same Playwright driver/protocol as #520's
before-table). Not run as part of #521's implementation; captured here
as a placeholder so the record has a single home once it lands.

Expected shape per the prediction above: port-blur → Start-enabled
< 3.1 s, first full render < 10 s, server-ready → 🟢 lag reduced (not
gated by #521's acceptance criteria, but expected to drop given
`find_server_by_port` shares the same name-first filter).
