# Handoff — 2026-08-26b (Windows seat): Ornith "won't load" → #503; autonomous-run #504

Second seat of the day. Opened on the 2026-08-26 handoff's ground (`main` @
`0803c7f`); closes with `main` @ `efe5b56` + this doc, six PRs merged, the
shared checkout clean and current.

## The operator's report, resolved

"It still loads the qwen3.5-27b, but not the newer entry Ornith."

**Not a config defect.** Ornith's entry validates, no denied flags, the 18 GB
file is present, and llama-server loads it cleanly in ~20 s. **Every** Start /
Swap — CLI and the Streamlit Models tab alike, whenever a local agent is
healthy — went over HTTP to the agent with `RemoteNode`'s hard-coded **5 s**
client timeout (`remote/node.py`, never overridden) against `/start` and
`/swap` routes that deliberately block for up to the **120 s** readiness
window. Any model that loads in > 5 s tripped the client; Qwen "worked" only
because it usually lands under 5 s or is already resident. The agent-side
swap keeps running after the client gives up, so the UI's "Eviction failed"
toast was a **false negative** on a swap that completed or rolled back on its
own.

Banked as **#503** (CLI evidence + UI/swap code trace); fix shape (a) —
per-verb client timeout, no wire change — ratified by the operator; option
(b) async start declined. Landed as PR **#511** (`efe5b56`): `RemoteNode`
start/swap use `httpx.Timeout(150 s, connect=5 s)`; every other verb keeps 5 s;
real-socket slow-agent test double; CLI + UI AppTest coverage. **Live
verification post-merge:** `llauncher server start Ornith… --port 8083` →
exit 0, `/v1/models` reports the canonical name, clean stop (comment on
#503). Caveat carried honestly: the model came up warm in ~1 s, so the live
run proves the happy path, not the > 5 s path — that path is exercised by
the 6 s real-socket tests, not by a cold live load.

Follow-up **#513** (`auto:draft`): swap's real worst case (stop + load)
approaches 2 × readiness; bound it or make swap pollable.

## autonomous-run 2026-08-26 — ledger **#504**

Operator: "Looks good — take this role." Batch = open `auto:fix`.

| # | PR | merge | outcome |
|---|---|---|---|
| #497 | #507 | `6e81277` | `state.refresh()` once per script run (2 review findings fixed pre-merge) |
| #462 | #505 | `faebded` | deny-list error names the config entry |
| #496 | #506 | `c2c6d79` | mcp lazy-singleton tests isolated from psutil + nodes.json |
| #501 | #508 | `30c1da8` | README paths; `agent-bg` documented absent, **not** implemented (gated on #493) |
| #498 | #510 | `da569a5` | remaining `st.rerun()` sites → one run per click |
| #503 | #511 | `efe5b56` | above |

Gates: baseline F0 = 57 Windows-seat env failures on `main` (all ⊆ **#364**,
updated 45 → 57); final suite 2173 collected / 57 failed = F0 / 0 new /
coverage 99.29 % ≥ 99. Opus-tier dispatched review on every PR.

Bounced at intake, returned to ProdM: **#330** (`auto:fix` removed —
close-or-hold is the call), **#338** → `auto:draft` (audit-read output shape),
**#405** → `auto:draft` (root cause unpinned).

## Live box state (read-only inspection, banked on #493)

- **Two agents bound to :8765**: the manual foreground one (PID 318992,
  127.0.0.1, since 09:23, owns the Qwen `llama-server` on :8081) and the
  **NSSM service, Running again since 10:30** (0.0.0.0) — contradicting the
  morning handoff, which recorded it stopped. Lifecycle truth is split.
- A stale **08-25** `llauncher-manager` + Streamlit pair (PID 368184) still
  alive under a VS Code terminal.
- Nothing was started, stopped, or restarted by this session beyond Ornith on
  :8083 (started/stopped twice for diagnosis and verification).
- Observed, not chased: `server status` showed Qwen's UPTIME pinned at "6s"
  across calls — display quirk candidate.

## Engine incidents (harness, for the next run)

1. Workflow `agent()` with the custom `reviewer` type + a schema never
   returned its verdict (4/4). Plain Opus agent for review works (5/5).
2. Workflow subagents cap one Bash call at ~120 s regardless of the requested
   timeout, and structured-output enforcement fires before a backgrounded run
   notifies — #503's gate was cut twice. The direct `Agent` tool honors the
   10-min cap and background-wait; the 13-minute suite was gated there.
   **Rule for next time:** gates live in direct agents, or the suite is split
   so every piece is < 2 min.

## Orient residue — still not dispositioned (third orient will re-surface)

- `CLAUDE.md` doctrine pointers stale on this host: the Linux `/srv/dev/...`
  path; `ALIGNMENT_ROADMAP.md` is at
  `harness-tools/docs/ground-physics/need_review/`, not beside the
  constitution.
- Unmerged: `docs/2026-08-24-windows-seat-handoff` (expired pull-back
  trigger; only home of the fire-map HTML), `test/windows-integration-real-lane`,
  `origin/docs/multiuser-migration-consolidation` (unattributed),
  `origin/fix/151-llauncher-env-rename` (#151, `user:gate`).
- PR **#495** (ADR-LLNCH-028, `auto:draft`) awaiting ratification; now seven
  commits behind `main` — rebase before merge.

## Open, with owners

- **`user:gate` — hosting posture (#493).** Now urgent in the concrete: two
  agents on one port. Options unchanged: (1) NSSM alone, (2) user-session
  ritual (+ `agent-bg` as its own `auto:fix`), (3) as-is. Whichever: stop the
  08-25 manager pair.
- `auto:draft`: #513, #338, #405, PR #495.
- ProdM call: #330 close or hold.
- `auto:fix`, ready: none from this batch remain open.

## Next step

Decide #493 — it gates whether the next session opens against one durable
agent or two competing ones. Then rebase + ratify #495. The `auto:fix` queue
is empty; the next run needs triage first (`triage-issues`).
