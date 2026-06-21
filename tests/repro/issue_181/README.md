# Deterministic, GPU-free repro set — issue #181

> **UI eject silently no-ops: UI running-state (argv-scan) diverges from
> lockfile authority; triggered by model_path collision.**

These modules **encode CURRENT BUGGY behavior** (RED-pins). Each `assert`s
that the bug is *present*, so a module **PASSES while the defect exists**.
When a defect is fixed, invert (or remove) the matching assertion — a failing
repro module is then the signal that the fix landed. Cross-reference issue
**#181**.

## How to run

```bash
tests/repro/issue_181/run.sh
```

Prints `REPRODUCED ✓` / `NOT-REPRODUCED ✗` per defect, runs a leak check, and
exits non-zero if any defect fails to reproduce or a fake process leaked.
Safe to run repeatedly.

## Safety design (why this can't perturb live state)

A prior repro run perturbed live infrastructure. This set is built so it
cannot:

- **No real services.** Never touches ports **8081/8082**, the real
  `llama.cpp` binary, the GPU, or the running llauncher agent (pid 35859).
  Everything uses throwaway port **18181** and **fake llama-server
  stand-ins**.
- **Fake llama-server = argv-only match.** The llauncher scanner
  (`core/process.py`) matches via
  `"llama-server" in proc.name() or any("llama-server" in c for c in cmdline)`
  and reads `--port N` / `-m PATH` from argv. A fake is a cheap long-lived
  sleeper whose **argv[0] is `llama-server`** (so the cmdline substring
  matches) carrying `-m /fake/issue181/... --port 18181`. It **binds no
  socket** and loads no model.
- **Hermetic on-disk state.** `LAUNCHER_RUN_DIR` / `LAUNCHER_AUDIT_PATH` /
  `LAUNCHER_LOG_DIR` are redirected to a per-process temp dir **before**
  llauncher is imported (`settings.py` snapshots them at import time, and
  `operations/stop.py` resolves the lockfile dir from that constant). The
  operations path therefore reads our **temp** lockfiles, never
  `~/.llauncher/run`. The real `~/.llauncher/config.json` is never read —
  `ConfigStore.load` is monkeypatched to return hermetic fixtures.
- **No leaks.** Live fakes are terminated **and** reaped; the deliberately-
  created zombie is reaped via `waitpid` in the same parent that forked it.
  `assert_no_fake_leaks()` runs in every module's `finally`, and `run.sh`
  re-checks `pgrep` for any surviving `--port 18181` / `/fake/issue181`
  process.

All real modules are **imported, not reimplemented** — the assertions run
against live llauncher code.

---

## Defect A1 — zombie blindness → eject no-op

`tests/repro/issue_181/repro_a1_zombie_blindness.py`

**Symptom (operator).** Clicking the live indicator's eject does nothing; the
model stays loaded. The UI's running-state is an argv scan of the process
table, and a `llama-server` that has become `<defunct>`/zombie is invisible
to that scan.

**Code path.**
- `core/process.py::find_server_by_port` (~line 444) and
  `find_all_llama_servers` (~line 467) call `proc.cmdline()` inside a `try`
  that catches `psutil.ZombieProcess` and `continue`s. A zombie's
  `cmdline()` raises `ZombieProcess`; its `name()` is the real exe, not
  `llama-server` — so neither match arm fires and the pid is skipped.
- `state.py::refresh_running_servers` (line 91) iterates
  `find_all_llama_servers()` → the zombie never enters `self.running`.
- `state.py::can_stop` (line 243): `port not in self.running` →
  `(False, "No server running on port {port}")` — the verbatim symptom.

**Expected-correct vs actual-buggy.** Correct: a still-present pid claiming
the port is stoppable (lockfile-authoritative, ADR-008). Buggy: the kill is
never attempted; eject is a success-shaped no-op.

**How the script demonstrates it.** Forks a fake bound conceptually to 18181,
SIGKILLs it without reaping (→ zombie, pid still exists), then asserts
`find_server_by_port(18181) is None`, the pid is absent from
`find_all_llama_servers()`, `18181 not in state.running` after `refresh`, and
`can_stop(18181, caller="ui") == (False, "No server running on port 18181")`.
Reaps in teardown.

## Defect A2 — scan vs lockfile divergence

`tests/repro/issue_181/repro_a2_scan_vs_lockfile_divergence.py`

**Symptom.** The UI eject path and the MCP/operations stop path use two
unsynchronized models of "what is running" and can give **opposite** answers
for the same port.

**Code path.**
- UI: `state.py::refresh_running_servers` (line 91, argv scan) →
  `state.py::can_stop` (line 243) → `(False, "No server running…")`.
- Operations: `operations/stop.py::stop` → `_reconcile_for_stop` (line 62) →
  `lf.read_lockfile(port)` returns the live claim → must terminate.

**Expected-correct vs actual-buggy.** Correct: both paths derive from one
authority (the lockfile). Buggy: scan and lockfile disagree; the UI refuses
while operations would stop.

**How the script demonstrates it.** Writes a valid lockfile for 18181 (in the
temp run-dir) pointing at a live fake, then asserts `_reconcile_for_stop`
hands back a live claim (stoppable) while `can_stop` over a stale/empty scan
snapshot returns `(False, …)`. The divergence is asserted explicitly
(`ops_stoppable != ui_ok`). It does **not** call `operations.stop.stop` (no
need to actually terminate to show the disagreement).

## Defect B — `_find_model_by_path` first-match misattribution

`tests/repro/issue_181/repro_b_model_path_misattribution.py`

**Symptom.** Two `ModelConfig`s share one `model_path` (live:
`Qwen3.5-27B-UD-Q6_K_XL` and `…-nommproj` → same `.gguf`). The running
server is attributed to the **first dict match**, which can differ from the
launched identity recorded in the lockfile/`--alias` (the mint).

**Code path.**
- `state.py::refresh_running_servers` (line 113) →
  `_find_model_by_path(model_path)`.
- `state.py::_find_model_by_path` (line 175) returns the **first** `name`
  whose `config.model_path` matches — insertion-order-dependent, ignoring the
  launched `--alias`.

**Expected-correct vs actual-buggy.** Correct: identity comes from the
lockfile's `model`. Buggy: the reverse-match returns the first-inserted
sibling.

**How the script demonstrates it.** Two siblings share one gguf; the
authoritative (launched) identity is the **second** dict entry and is
recorded in the lockfile. After a real `refresh_running_servers`, asserts the
reported `config_name` is the dict-first sibling and **differs** from the
lockfile identity.

## Defect C — bogus `start_time` / `uptime_seconds`

`tests/repro/issue_181/repro_c_bogus_uptime.py`

**Symptom.** Reported uptime is always ~0.

**Code path.**
- `state.py::refresh_running_servers` (line 119) hardcodes
  `start_time=datetime.now()` ("We don't track actual start time").
- `models/config.py::RunningServer.uptime_seconds` computes
  `datetime.now() - start_time` → ≈ 0.

**Expected-correct vs actual-buggy.** Correct: `start_time` derives from the
lockfile's `started_at`. Buggy: it's `now()`, so uptime ≈ 0 regardless.

**How the script demonstrates it.** Writes a lockfile with `started_at` ~2h
ago, scans a live fake, asserts the reported `start_time` is ~now (between a
`before`/`after` bracket) and `uptime_seconds() < 5`, i.e. the lockfile's
real age (~7200s) was ignored.

---

## Suggested fix directions (from issue #181, for triage)

1. Make the UI eject path lockfile-authoritative (route UI stop through
   `operations/stop.py::stop`, or have `can_stop`/`refresh_running_servers`
   consult lockfiles) — closes A1 and A2.
2. Derive running `config_name`/identity from the lockfile, eliminating
   `_find_model_by_path` reverse-matching — closes B and C.
3. Unify the audit channel so UI actions land in the persisted `audit.jsonl`.
4. Tighten process matching to argv[0]/exe-link (Defect D in the issue — not
   reproduced here; latent, not implicated in the incident).
