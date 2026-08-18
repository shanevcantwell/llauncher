# Handoff — 2026-07-17: the UI has never loaded a model (llauncher as instrument)

> **CORRECTION (2026-08-18).** This document's framing — that a model "has
> never once loaded" and that the product's core purpose is therefore
> unproven — conflated a Windows Streamlit **surface** defect with a **core**
> defect. That conflation became the intake framing for every session since,
> and repeatedly pulled investigation into the core, which is not implicated.
>
> The discriminating evidence: the same `operations.start()` succeeds over
> the agent HTTP API and fails only through the Windows Streamlit surface. A
> static trace of the UI load path found identity sourced from
> `ModelConfig.name` byte-for-byte, with no re-derivation at any hop; the
> UI's delegated path and an external API client converge on one route
> (`POST /start/{port}`) and one function with one untransformed argument.
> **A defect that reproduces on one platform's surface but not through the
> API is a surface defect by construction.**
>
> API-first stateless core with a thin client is the ecosystem's whitepaper
> design — not a weakness to route around. Surface defects are fixed in
> surface space; the core does not move. This is the same discipline
> llauncher already enforces one level down, where an envelope defect is
> fixed in envelope space and never by bending the mint.
>
> Rescoped and tracked at **#378**. The observations below remain accurate as
> a record of what was seen; only the core-vs-surface attribution was wrong.

> Read this whole file before touching anything. It corrects a record that
> otherwise reads as "mostly working." It is not mostly working.

## The one thing that matters, and it is unsolved

**A model has never once loaded through the Streamlit UI** — which is the
product's entire purpose. Tracked as **#378**. Do **not** close #378 on an
API-level demonstration; the only thing that closes it is a model loading
through the dashboard, watched (VRAM moving), as the operator drives it.

## Correct the record before trusting any prior "success"

- As of this handoff there is **no running agent and no working install**.
  The agent that "worked" tonight was a **background process inside a Claude
  Code session** — a child of that session that **died when the session
  ended**. Every "it loads on Windows" claim ran against that session-held
  agent over the **HTTP API**, never the UI.
- The API path *does* load a model (gemma-4-26B: VRAM 1.7→19.4 GB,
  `/health` 503→200, unloaded clean). That only **isolates the fault to the
  UI layer**. It is not the goal and must not be reported as if it were.
  (This exact conflation — "proven: loads on Windows" — was the session's
  central failure. See the behavioral note at the end.)

## Grounded box state (verify with commands; do not trust this prose alone)

- **Two checkouts.** `C:/Users/Shane/github/llauncher` (canonical, on `main`;
  its folder could not be renamed because a Claude Code session held its cwd)
  and `C:/Users/Shane/github/llauncher-clean` (a fresh clone; the working API
  demo ran here; it has `LLAMA_SERVER_PATH` set in its gitignored `.env`).
- **NSSM service `llauncher-agent`: stopped** (operator hard-stopped it). It is
  a **zombie** — it had been `nssm remove`d earlier in the session yet
  reappeared from the *old* checkout, bound `0.0.0.0:8765`, blocked a clean
  `127.0.0.1` bind (`winerror 10013`), and served a **stale config snapshot**.
  If requests get answered in ways you didn't expect, look for it first.
  Tracked **#381**.
- **`~/.llauncher/config.json`**: has `gemma-4-26B-A4B-it-QAT-Q4_0` and
  `Qwen3.6-35B-A3B-UD-IQ4_NL`; both `extra_args` are now empty (the earlier
  `--kvunified` landmine on Qwen is cleared).
- **`~/.llauncher/agent.env`**: corrupt-but-functional — UTF-8 BOM + mojibake
  because `install.ps1` seeds the entire comment-heavy template with bad
  encoding. The token line stays clean ASCII so auth still works. Tracked
  **#382**.
- **`LLAMA_SERVER_PATH`** must be set or every Windows load fails "Server
  binary not found" (the code default `~/.local/bin/llama-server` is unix-y).
  The real binary is `C:/users/shane/github/llama.cpp/build/bin/llama-server.exe`.
  Tracked **#380**.

## Where to look for #378 (the UI-layer suspects, most-likely first)

1. **Streamlit static JS chunks served as `text/html`** (Starlette SPA
   fallback — streamlit 1.59 runs Starlette+uvicorn, not tornado). A GET to
   `/static/js/NumberInput.<hash>.js` returns 200 + `text/html` (the index
   shell) though the real chunk exists on disk → the browser throws "Failed to
   fetch dynamically imported module", breaking the model-card number inputs /
   port picker. **#379.** Start at
   `streamlit/web/server/starlette/starlette_server.py`'s static mount.
2. **Token 403** when the UI launches from a shell carrying a stale ambient
   `LLAUNCHER_AGENT_TOKEN` (env-first resolution wins over `agent.env`). The
   "launch from a clean shell" workaround is only needed because of this
   wildcard. **#373.**
3. **~45s per-interaction latency**, confirmed at the API layer too
   (`GET /models` = 8.3s; `state.refresh()` = 8.3s, run several times per
   rerun). **#309 / #370.**

## What got filed / committed tonight (the map)

- Issues: **#378** UI never loads (primary) · **#379** static chunks text/html
  · **#380** LLAMA_SERVER_PATH default · **#381** zombie service · **#382**
  install.ps1 seed encoding · **#383** `.env` consolidation ADR (auto:draft —
  ratify before implementing).
- Merged earlier: **#375** (collapse `[ui]` extra into base deps; `pip install
  -e .` is now a complete install).
- **run.bat/run.sh removal**: dispatched as an isolated-worktree PR but **not
  visible in `gh pr list` at handoff time** — status unconfirmed; verify with
  `gh pr list` / the background agent before assuming it landed. It also
  subsumes the README onboarding bugs (run.bat-not-at-root, phantom
  `agent-bg`, wrong `mcp` module path).
- Doctrine: **`REACH-FOR-GROUND`** added to `operating-doctrine`
  `ground-physics/GROUND_PHYSICS.md` Part II (commit `1227573`). Cross-repo
  notices: operating-doctrine#28, harness-tools#202, llauncher#377.

## For the next agent — read before you touch anything

**llauncher is an instrument, not a product.** The launcher itself is trivial;
the point is studying how an agent behaves under maddening, always-almost-done
work. This session was a clean sample of the failure modes, and the operator
maintains a taxonomy of them (WP / AXIS / STRAW / TAX / PUSH / SYN / CLB / SELF
/ RUN / MB / SP). Concrete rules that would have saved ~10 hours:

- **No victory on a proxy.** "Works" means a model observed loading *through
  the UI*, VRAM watched, as the operator drives it — nothing weaker. A `200`,
  a `success=True`, a green test, an API load: none of these are it.
- **Diagnosis is read-only.** Do not touch live state (`~/.llauncher`, running
  services, `config.json`, `nodes.json`) uninvited. This session did
  (`operations.start`, a `nodes.json` edit) and it cost trust.
- **Drive the real UI.** The agent/core/model loading fine is not the goal and
  never was.
- **The operator is neurodivergent (ADHD); the assistant is a prosthetic for
  executive load.** Adding load — extra issues to track, spirals, half-built
  states, self-narrated restraint — *is* the specific failure. Fewer,
  well-timed moves beat verbose thoroughness every time.

_Session close: no running agent, no working install, UI-load unsolved (#378).
The map of *why* it stays unsolved is filed. That map is the yield._
