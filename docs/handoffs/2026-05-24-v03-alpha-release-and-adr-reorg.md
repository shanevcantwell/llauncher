# Session handoff — v0.3.0-alpha release + ADR reorganization (2026-05-24 PM)

Companion to `docs/handoffs/2026-05-24-v2-closeout-and-ui-design-scope.md` (same day, AM session). Pointers to source files inline; artifact contents not pasted here.

## 1. Where we are

Branch `main` at `5daa8ac` (`docs(adrs): file 15 root ADRs into completed/accepted/superseded folders`). Tag `v0.3.0-alpha` pushed; GitHub pre-release published at <https://github.com/shanevcantwell/llauncher/releases/tag/v0.3.0-alpha>. Working tree clean; `origin/main` synced.

Pre-release version `0.3.0a0` (PEP 440). The `v2.0.0` tag remains reserved for the M7 release after the vLLM backend adapter (#42) and the pi-footer TypeScript migration, per the roadmap.

## 2. What landed

### Commits (PM, chronological)

| Commit | Title |
|---|---|
| `020d61d` | `docs(v0.3.0-alpha): sync user-facing docs with v2-final state` |
| `413874a` | `chore(release): bump version to 0.3.0a0 for v0.3.0-alpha tag` |
| `f63213d` | `chore(tooling): add summarize_tests.py + retire stale test-count claims` |
| `5daa8ac` | `docs(adrs): file 15 root ADRs into completed/accepted/superseded folders` |

Plus annotated tag `v0.3.0-alpha` on `413874a`; GitHub Release with auto-generated changelog of 20 PRs since `v0.2.0-alpha` and a mission-framing header.

### Docs audit pass (`020d61d`)

Audit found `README.md` and `docs/MCP.md` substantially stale against M3/M4/M5 + security cohort work. Mission framing reasserted across both: MCP is the canonical contract; HTTP Agent, CLI, and Streamlit UI are co-equal consumers of the same `llauncher/operations/` service layer.

- `README.md` — Features section rewritten with **Core** subsection above MCP/UI/CLI to surface the shared `operations/` layer; CLI co-billed with MCP and UI; tab structure corrected to Dashboard/Models/Nodes/Audit (two stale lists fixed); both auto-spawn contradictions removed (L155 and L380); CLI section rewritten as real Typer reference; `default_port` stripped from config example; project tree regenerated.
- `docs/MCP.md` — Tool count 11 → 13 (`cancel_server` and `list_orphans` added); `remove_model` → `delete_model` throughout; HTTP endpoint table rewritten port-keyed; Script Discovery section deleted; env-var table replaced with the real `LAUNCHER_*` family; `default_port` stripped from 8 example payloads; `start_server` documented with required `port`.
- `docs/v2-handoff.md` — Dangling `docs/PRODUCT_REQUIREMENTS.md` references removed (file doesn't exist). §Institutional Knowledge #1 rephrased to preserve the "don't blindly re-derive from v1" lesson without the dead pointer.
- `docs/{1,2,3,4}-*.md` — HISTORICAL banner added to each. These are pre-v2 artifacts from the April audit cohort; the "four LauncherState instances" framing in #4 is literally what `operations/` resolved.

### Tooling: `scripts/summarize_tests.py` (`f63213d`)

Stdlib-only (`ast`, `os`, `pathlib`), ~210 LOC. Hybrid path+marker categorization adapted from sibling repos (`langgraph-agentic-scaffold` for the walker shape; `semantic-forge` for the marker extraction pattern).

- **Primary axis (path):** `tests/unit/` → unit, `tests/integration/` → integration, else → other.
- **Secondary axis (markers):** the four llauncher-meaningful markers — `integration`, `integration_real`, `live`, `real_model_health`. Pytest builtins (`asyncio`, `parametrize`, `skip`, `skipif`, `xfail`, `usefixtures`) are filtered as noise. Class-level marks on `Test*` classes propagate to methods per pytest semantics.
- Output: `docs/generated/TEST_SUITE_SUMMARY.md` (overwrote an `a67e3ea`-era stale copy at 34% coverage; current 65 files / 983 tests).
- Manual-regen (`python scripts/summarize_tests.py`). Header explicitly delegates live pass/skip counts to pytest; this document is an inventory of *which* tests exist, not which currently pass.
- **Count discrepancy is by design:** script reports 983 (static AST), pytest reports 1020 collected / 1009 ran (parametrize expansion). Don't reconcile.

Same commit retired stale "728 tests pass / 10 skipped" verification surfaces in `v2-handoff.md` (§Known Failures L206, §Pre-Work Verification L294); historical counts in completed-milestone sections (L56, L78) left as-is.

### ADR reorganization (`5daa8ac`)

Fan-out verification across all 15 root ADRs against current code. Three in-doc Status fields were stale; one used non-canon "Approved"; one had a duplicate `## Consequences` section.

**New taxonomy** (extends existing `completed/` convention):
- `completed/` — accepted, no open implementation gaps. **10 ADRs:** 001, 003, 005, 007, 009, 010, 011, 012, 014, 016.
- `accepted/` — accepted with known partial gaps tracked as issues, or scope explicitly deferred in §Deferred Work. **5 ADRs:** 004, 006, 008, 013, 015.
- `superseded/` — replaced by a later ADR. **1 ADR:** 002 (by 011).
- `draft/` — not yet ratified. **Currently empty** — no Draft ADRs survived verification.

**Status field fixes (in-doc):**
| ADR | Was | Now |
|---|---|---|
| 001 | `Approved` (non-canon) | `Accepted` + dropped non-canonical `Approved by:` line |
| 004 | `Draft` | `Accepted — partial implementation` + Amendment Notes |
| 005 | `Draft` | `Accepted` |
| 006 | `Draft` | `Accepted — partial implementation` + Amendment Notes |

**Other in-doc cleanups:**
- ADR-003 duplicate `## Consequences` block (L80–95) merged into the canonical L64 copy; Amendment Notes added covering PR #75 (loopback default + non-loopback token requirement) and PR #87 / C1 (`create_app` token requirement); Open Question #1 marked resolved.
- ADR-004 Amendment Notes list shipped subcommand groups (model/server/node/config + bonus `server cancel` from ADR-014, `orphan` group from ADR-015) and the two deferred verbs (`swap`, `logs`). Open Questions resolved against ADR-010.
- ADR-006 Amendment Notes list shipped (NVIDIA backend, `/status` integration, VRAM pre-flight) and deferred (`?full=true` filter, ROCm/MPS backends, #44). Endpoint-shape drift documented: original example `POST /start-with-eviction/{model}?port={p}` was replaced by port-keyed `POST /swap/{port}` per ADR-010/011.

**New file:** `docs/adrs/README.md` — taxonomy table + per-folder index with one-line "what's deferred" notes for each `accepted/` entry. Conventions for moving ADRs between folders.

**Live path references updated:** `docs/v2-handoff.md` §Where Things Live + §Conventions; `docs/examples/self-swap-timeline.md` (3 relative links); `docs/plans/v2-implementation-roadmap.md` (1 ref). Historical refs in `_audit_*.md`, `PLAN-architectural-remediation.md`, `sleeptime-remediation/`, and `m3–m6-design.md` left as archaeological snapshots.

## 3. Notable findings worth carrying forward

1. **`docs/PRODUCT_REQUIREMENTS.md` was a dangling reference** in the frozen handoff at two locations. Fixed; the handoff is the canonical statement now.
2. **ADR-003 had a duplicate `## Consequences` section.** Latent doc-hygiene bug, undetected for months. The fan-out verification surfaced it.
3. **Two pytest markers are undeclared in `pytest.ini`:** `integration_real` (1 use) and `real_model_health` (9 uses; documented as intentional in handoff §Institutional Knowledge #11). If `--strict-markers` is ever adopted, these need either declarations or a sweep.
4. **ADR-006's original spec used a v1-era endpoint shape** (`/start-with-eviction/{model}?port={p}`); the live code has port-keyed `/swap/{port}` per ADR-010. Drift documented in 006's Amendment Notes — useful prior pattern when refactors leave ADRs behind.
5. **`docs/handoffs/` convention reinforced:** per-session capsules are immutable; multiple files for the same date are fine when distinct slices warrant it (this dossier + the AM counterpart).

## 4. Tracking artifacts filed this session

New issues opened from surface findings (per docs convention — follow-ups live in GH Issues, not dossiers):

- **#123** — `docs(scripts/windows): document LLAMA_SERVER_PATH in agent.env.example`. Surfaced via the operator Q&A about changing `LLAMA_SERVER_PATH` for an installed Windows service. The project-tree `.env` works as a fallback by coincidence-of-defaults (`AppDirectory` + `load_dotenv()` search-from-CWD), not by design; the canonical channel is `%USERPROFILE%\.llauncher\agent.env` but the template doesn't say so.
- **#124** — `tooling: drift detection for TEST_SUITE_SUMMARY`. Inventory file is committed but manual-regen only; nothing prevents drift between commits.

Carried-forward (not new this session, already known):

- The pre-existing handoff recommendation that "Conventions / What NOT To Do / Institutional Knowledge sections should migrate to a repo-root `CLAUDE.md`" — see `docs/v2-handoff.md` introduction (2026-05-24 freeze). Not actioned during v0.3.0-alpha; remains a design observation on the handoff itself, not a separate trackable item.

## 5. Carry-forward (open issues)

- **#117** (cross-repo doc link for ADR-016, blocked on sibling pi-coding-agent repo) — v2-final, non-blocking.
- **#122** (Streamlit `use_container_width` deprecation migration) — v2-final, non-blocking.
- v3-alpha: #119 (UI IA pass), #120 (`--alias` to llama-server), #69 (Streamlit AppTest harness).
- v4-alpha: #121 (model-card surface).
- M6: #42 (vLLM backend adapter) — gates `v2.0.0`.

## 6. Pre-work verification (next session)

```bash
# State
git log --oneline -6           # tip should be 5daa8ac or later
git tag -l 'v0.3.0-alpha'      # should print
gh release view v0.3.0-alpha   # should show prerelease

# Tests (live counts drift — inventory in docs/generated/TEST_SUITE_SUMMARY.md)
python -m pytest tests/ -q | tail -3

# ADR layout
ls docs/adrs/{completed,accepted,superseded}/

# Open Issues (v2-final remainder + active milestones)
gh issue list --milestone v2-final --state open
gh issue list --milestone v3-alpha --state open
gh issue list --milestone v4-alpha --state open
```
