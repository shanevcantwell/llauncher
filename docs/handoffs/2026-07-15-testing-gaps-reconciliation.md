# Handoff — 2026-07-15 — testing-gaps reconciliation (all-surfaces E2E)

**State in one line:** a two-platform "can't bring a model up" day resolved into a **testing-coverage
reconciliation**: the whole class of bug (Windows silent-500, Linux "Authentication required")
ships invisibly because **E2E does not go through all surfaces** — `ui/` and `cli.py` are mock-only,
and the one seam the auth bug lives in (a real `X-Api-Key` over a real socket) is untested. The
governing decision for this (**ADR-LLNCH-018 / #168**) already exists but has sat un-ratified since
2026-06-13. **Non-live AND live e2e are now green on `main`** — the blocking fixture bug (#316) was
fixed, Opus-reviewed, and merged this session (`03dd9f8`); both live-model tests pass.

Times: UTC on the host; operator is US/Mountain (UTC-6, MDT).

---

## Landed to `main` this session
| what | ref |
|---|---|
| install.ps1 repoint NSSM `Application` on every run (+ enforcement test, + #305 allowlist unblock) | **#314** → PR **#315** squash-merged as `a081ac2` |
| local `main` fast-forwarded `0737f20 → a081ac2` | — |
| validate-config-entries-have-live-models (enhancement) | **#313** filed |

**Non-live full suite: GREEN** — `1503 passed, 19 skipped, 0 failed`, coverage 99.89% (verified at `a081ac2`).

## Filed this session — the testing gaps (the operator's "file everything")
| # | gap | label | status |
|---|---|---|---|
| **#316** | live e2e blocked: `mcp_env`+`real_binary_env` both `mkdir tmp_path/run` (`FileExistsError` under `LLAUNCHER_INTEGRATION_REAL`) | `bug, auto:fix` | ✅ **MERGED `03dd9f8`** (PR #321) — live e2e green on `main` |
| **#317** | no real `X-Api-Key` over a real socket to a real agent (200 vs 401) — the seam "Authentication required" hides in | `enhancement, auto:fix` | greenfield, **buildable now** |
| **#318** | declare `integration_real`/`real_model_health` in `pytest.ini` + adopt `--strict-markers` | `enhancement, auto:fix` | greenfield, small |
| **#319** | Linux "Authentication required" = multi-user state-dir token split (`llauncher`/`shane`/`claude` each resolve `~/.llauncher` differently) | `bug, user:gate` | **perm-blocked** — needs operator sudo-read |
| **#320** | no live UI/CLI→agent→model vertical slice (all-surfaces coverage gap) | `enhancement, auto:draft` | **gated on #119** UI redesign |

---

## THE RECONCILIATION PLAN

### The finding
A true E2E must exercise all four architecture doors: `agent/` (HTTP), `mcp_server/` (stdio),
`ui/` (Streamlit), `cli.py`. Today's coverage:
- **`agent/` + `mcp_server/`**: live coverage exists (integration tests drive a real `llama-server`
  stub subprocess via in-process `TestClient`/`mcp_dispatch`).
- **`ui/` + `cli.py`**: **100% mocked** — never driven against a live backend of any kind. The
  `AppTest` harness (PR #251) is *designed* to forbid real sockets (`forbid_direct_http`).
- **Real auth over the wire**: **untested anywhere.** `test_agent_security_c1_c2.py` explicitly
  declines to bind a socket. This is exactly where "Authentication required" lives.
- **Coverage gate exempts the UI**: `pyproject.toml` still `omit = ["llauncher/ui/*"]`, floor is
  `--cov-fail-under=93` on the *non-UI* scope. So a regression in the UI→agent→model path sails
  through the gate — the structural reason this class of bug is found in the field, not at the gate.

### Prior decisions to HONOR (do not relitigate)
- **UI-tab AppTest coverage is deliberately deferred behind a UI redesign.** #69 was *closed as
  blocked* ("writing AppTest coverage against tabs about to be restructured is wasted work"). The
  redesign is **#119** (open, `pri:next`). Do **not** build per-tab UI tests before #119.
- **ADR-LLNCH-018 / #168 is the governing decision** for "close the UI coverage exemption" — a
  comprehensive draft (100%-of-exercised floor, `branch=true`, drop the `ui/*` omit, `xfail(strict)`
  deferral ledger, exercise `ui/*` via `AppTest`). It is **DRAFT, awaiting operator ratification.**
  Two wrinkles at ratification: (a) the number **018 is already taken** by the accepted systemd ADR —
  renumber; (b) #290 is a related mis-numbered salvage (ADR-LLNCH-019 collision) cross-referencing #168.
- **Browser/Playwright E2E is explicitly out of scope.** Settled.

### The unlock
The "Authentication required" bug is an **auth round-trip** failure, testable at the
`remote → agent` **socket** layer — **independent of the UI tab structure**, so it does *not* wait
on #119. That is **#317**, and it is the missing enforcement surface for the entire class of auth
bug that ate 2026-07-15 on both platforms.

### Dependency-ordered sequence
1. **#316 — fixture-collision fix** (auto:fix) — ✅ **DONE this session** (merged `03dd9f8`, PR #321;
   Opus-reviewed SHIP). Unblocked live e2e; both `integration_real` tests now pass against real
   models. Non-live regression guard added (`tests/integration/test_conftest_fixtures.py`).
   Banked follow-up nit: the composition test could also assert the stub→real binary override took
   effect, not just the mkdir.
2. **#317 — real-socket auth vertical slice** (auto:fix, buildable now, decoupled from #119).
   Bind a real agent on a real port; real `RemoteNode` with `X-Api-Key`; assert 200 (good token) vs
   401/403 (wrong/missing) vs correct framing with a CRLF/BOM token (#310/#127 guard).
3. **#318 — marker hygiene** (auto:fix). Declare the markers in `pytest.ini`; adopt
   `--strict-markers`. Needed by ADR-LLNCH-018's deferral-ledger discipline anyway.
4. **ADR-LLNCH-018 / #168 — ratify + execute (operator decision).** Renumber (018 taken). Amend to a
   *phased* plan: land `branch=true` + the reason-required deferral ledger + #317 now; **gate the
   floor-flip-to-100, the `ui/*` omit-drop, and UI-tab AppTest coverage on #119.** This makes
   all-surfaces coverage a *requirement*, not a draft.
5. **#320 — live UI/CLI vertical slice** (auto:draft, **gated on #119**). The UI/CLI-render →
   live-backend half. Reuse `docs/plans/streamlit-ui-harness-plan.md` once #119 lands.

### Separate track (not a testing gap — operator-gated)
- **#319 — Linux runtime token split** (`user:gate`). The live "Authentication required" fix.
  Confirming needs sudo-reads denied to the `claude` seat: the systemd unit's `Environment=`
  (the agent's real `LAUNCHER_STATE_DIR`) and `shane`'s `agent.token` vs the agent's. Same class as
  the Windows LocalSystem wrinkle (#284/#292). **Green tests will NOT fix this** — it's runtime, not
  code.

---

## Landed after the plan was drafted (same session)
- **#316 fixture fix — MERGED** (`03dd9f8`, PR #321, Opus-reviewed SHIP). Live e2e re-baseline
  **achieved**: `test_self_swap_live_completion_against_new_model` +
  `test_server_metrics_live_reports_phase_and_rate` both PASS against real gemma-4-E2B / Qwen3.5-4B
  via real `llama-server`. Non-live suite green, 99.92% coverage. Nothing in flight at handoff.

## Parked (from earlier today, unchanged)
- **Windows visibility branch** `fix/windows-runtime-visibility-128-307-308` (#128/#307/#308) —
  pushed, **not merged**, awaiting on-box field-confirm. Deploy blockers found & fixed en route:
  #314 (Application repoint), plus the operator's clean re-clone. The service must run from the
  clone's venv (editable) — see #314.
- **#291** 8-PR stale pile — land-or-reconcile, untouched.

## Environment / access — for the next session to run live e2e
- **llama-server binary:** `/srv/dev/llama.cpp/build/bin/llama-server`
- **Small completion GGUF:** `/home/claude/.cache/huggingface/hub/models--unsloth--gemma-4-E2B-it-GGUF/snapshots/f064409f340b34190993560b2168133e5dbae558/gemma-4-E2B-it-UD-Q4_K_XL.gguf`
- **Second model (swap):** `/home/claude/.cache/huggingface/hub/models--unsloth--Qwen3.5-4B-GGUF/snapshots/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-UD-Q4_K_XL.gguf`
- **Run command:**
  ```
  env -C /srv/dev/shanevcantwell/llauncher \
    LLAUNCHER_INTEGRATION_REAL=1 \
    LLAMA_SERVER_PATH=/srv/dev/llama.cpp/build/bin/llama-server \
    LLAMA_SMALL_GGUF=<gemma path above> \
    LLAMA_SMALL_GGUF_B=<qwen path above> \
    /srv/dev/shanevcantwell/llauncher/.venv/bin/python -m pytest -q -rA
  ```
- Live tests spin up their **own** ephemeral `llama-server` — they do NOT need the resident
  `:8081`/`:8082` (both down) or the systemd agent. GPU (RTX 8000) ~46 GB free.
- **llauncher MCP control tools are NOT exposed in this Claude Code session** — could not drive host
  model servers up/down from the seat. Wire the MCP in, or the operator drives the runtime.

## Recommended next-session order
1. ✅ **#316 done** — live re-baseline green on `main`. (Optional: apply the banked review nit.)
2. Build **#317** (real-socket auth vertical slice) — the enforcement surface for today's outage.
   Highest-value unblocked build; decoupled from #119.
3. **#318** marker hygiene.
4. Bring **ADR-LLNCH-018 / #168** to the operator for ratification (renumbered, phased). Execute the
   unblocked steps.
5. **#319** with the operator (sudo-reads) — fix the live Linux UI auth.
6. **#320** stays gated on **#119**.
