# Handoff — 2026-07-14 — installer fixes + Windows runtime triage

**State in one line:** the Windows token-recurrence arc is *closed and field-confirmed*; the
installer now parses and runs; the session pivoted into **Windows runtime triage** (a silent 500
on start, UI perf, observability) which is diagnosed-and-banked but not yet fixed. Ground clean on
`main` @ `64bcd30` (+ this handoff commit).

---

## Landed to `main` this session

| PR | issue(s) | what |
|---|---|---|
| #297 | #287 | test flake — path-scoped a class-level `Path.stat` patch |
| #296 | #293, #285 | 403-recurrence: token rewrite-in-place + installer dedupe + runtime fail-loud guard |
| #302 | #300 | `install.ps1` de-Unicode to pure ASCII + Linux ASCII-guard test (`tests/architecture/test_ps1_ascii.py`) |
| #305 | #303 | `MigrateEnvKeys.ps1` `-Lines` param `[AllowEmptyString()]` (blank-line env files) + pwsh-gated regression test |

## Confirmed in the field
- **#299 CLOSED** — 403s cleared on the Windows box: `/node-info` 200, `POST /start` reaches the
  handler (500, not 403). The whole token arc (#293/#285 code + #300/#303 installer) is validated.

---

## Open bugs filed/updated this session

**Windows runtime (the live triage front):**
- **#308** `POST /start/8081` (embeddinggemma) → **500, silent**. `sev:crit`. **Diagnosed:** the
  invisibility is **#128** (traceback emitted to stderr but block-buffered under NSSM, never
  flushed to the tailed log — *not* a code swallow). Candidate cause "config `ValidationError`"
  **ruled out** (live `config.json` scan clean). Remaining: uncaught `OSError` from
  `al.record()`/`audit.jsonl` or `mk.take_marker`/`LAUNCHER_RUN_DIR` under the NSSM service
  account. **NOT VRAM** (22.4 GB free). Fix: land #128 first (reveal traceback), then wrap
  `start_server` handler with `logger.exception` + structured error body + tighten
  `operations/start.py:216` except.
- **#309** perf — `/status` measured at **8.47s** live; the 45s hang is this compounding + failed-start
  timeout; 10s tabs, 2–5s toggle. Blocking per-rerun backend I/O (nvidia-smi GPU query + process
  scan). Not VRAM.
- **#307** uvicorn access log has no timestamps. `auto:fix`. Diagnostic enabler.
- **#310** cross-platform Linux→Windows CRLF/BOM robustness — token/env reads must absorb `\r`/BOM
  transparently (agents shouldn't have to think). `auto:fix`. (Observed: grepping the CRLF
  `agent.env` captured a `\r` into the token → broke `X-Api-Key` framing.)
- **#311** agent self-reports empty `node_name` (`""`) on `/health` + `/node-info`. Relates #174.

**Design / larger:**
- **#292** UI "writes not persisting" = **`LAUNCHER_STATE_DIR` split-brain** (stray hand-launched UI
  wrote `~/.llauncher`; systemd path `/var/lib/llauncher` is stale). Operator waved off the
  migration; direction is systemd control. Remaining: `auto:draft` parity-check hardening (startup
  banner + `llauncher config path`). **#181 ruled out** for this incident.
- **#294** promote spec-decoding flags to first-class `ModelConfig` fields. `auto:draft`, awaiting ratification.
- **#298** installer same-pass dedupe + guard-message accuracy. `auto:fix`, `pri:later`.
- **#304** **Windows CI runner** for pwsh-gated installer tests — must exercise **PS 5.1** (pwsh 7
  would miss #300). `user:gate`. The durable fix so #296-class PS1 bugs stop shipping unverified
  (two did: #300 encoding, #303 logic).
- **#306** installer regression-test architecture (functional-core/imperative-shell; mock-seam +
  dry-run + CI). `auto:draft`, **PARKED** by operator — do not design yet.

## Pre-existing, now load-bearing
- **#128** NSSM runtime-logging capture gap — **elevated to keystone**: it is the root of #308's
  invisibility and every future Windows runtime bug's. Fix first.
- **#291** — the 8-PR stale pile, land-or-reconcile (NOT run this session). `#166` (env-rename #151)
  sits in the code #296 just rewrote — reconcile there first. Operator doctrine: **PRs are
  post-ratification; PR-stage = land-or-reconcile, never re-approve.**
- **#174** node-identity mint (#311 is the observed instance). **#188** embeddinggemma nonpooled 400
  (distinct from #308's 500 — client-side, after serving).

## Run-ledgers
- **#295** — 403-recurrence + UI-persist run. **#301** — installer-hygiene run (#300/#303).

---

## Environment / access — IMPORTANT for the next session
- **Windows agent reachable from the host at `http://192.168.137.1:8765`** (Shane-PC, RTX 3090,
  Win 11 build 26200, Python 3.13.7). `/health` is auth-exempt; authed endpoints need `X-Api-Key`.
- **Token** is on the mount at `/mnt/Users/Shane/.llauncher/agent.env` — it is a **CRLF file, strip
  `\r`** (`grep ... | tr -d '\r\n'`) or the header framing breaks. (This is #310.)
- **GPU:** 22.4 GB free VRAM, 1.9 GB used — not VRAM-bound.
- Mount `/mnt/Users/Shane/.llauncher/` holds `agent.env`, `config.json`, `logs/`.

## Recommended next-session order
1. **#128 + #307** — unbuffer NSSM logging (`PYTHONUNBUFFERED=1` / a `FileHandler`) + access-log
   timestamps. This makes every Windows runtime bug *visible* — keystone.
2. **#308** — with the traceback now visible, pin the actual start-500 cause (likely audit/marker
   write perms under the service account) and land the code-level surfacing fix.
3. **#309** — profile the `/status` 8.5s; bound/cache per-rerun I/O without breaking the stateless facade.
4. **#291** — land-or-reconcile the PR pile (`#166` first).
5. Ratify **#294**; decide **#292** parity hardening; **#306** stays parked; build **#304** when ready.

## Operator doctrine captured this session
- **PRs are post-ratification** — PR-stage is land-or-reconcile, never re-approve; no PR-approval gate.
- **"nit" = bug** — do not soften defects as nits.
- **Cross-platform Linux→Windows must work without agents thinking** (#310).
- **Merge-on-green** for `auto:fix` (approval is a pointless gate); correctness review stays.
- Installer PS1 path is **unverified without a Windows CI runner** (#304) — inspection + operator
  field-confirm is the current, honest limit.
