# Handoff: Windows seat continuation — 2026-08-24

Canonical live copy: **issue #468** (this file is the committed snapshot; #468 carries
any post-session updates). Session record: #309 (perf dossier), #464/0b756b8 (part-1
dedup, merged), #466 (pid-first anchor), #378 (state-dir diagnosis + acceptance
evidence), #462, #463 (isolation plan, ratified with riders), operating-doctrine#75.

## Runtime state at session end

- NSSM `llauncher-agent`: **STOPPED** (deliberate). User-session agent + Streamlit UI
  were children of the session's shells — assume down. Agent must run user-session
  until the LocalSystem state-dir env-delivery defect is fixed (#378 comments; home
  #383/#366).
- qwen `ctx_size` = 65536 (audited). 64K dashboard load unverified; 128K needs #416.

## Continuation order

1. Restart agent on post-#464 code; measure cold `GET /status` live (~12.6 s expected,
   2 scans); update fire-map artifact (hatched → solid). Optional: py-spy sampled graph.
2. After #463 lands (Linux seat): pull; run the Rider-1 falsifier on THIS box — bare
   anchor set adds zero lines to `C:\Users\Shane\.llauncher\audit.jsonl`.
3. Service env repair (#383/#366): why `AppEnvironmentExtra` `LAUNCHER_STATE_DIR`
   (REG_MULTI_SZ, verified well-formed) doesn't land in the process; fix; Start-Service;
   `GET /models` = 4 entries via service path.
4. #378 close decision (acceptance met 2026-08-24; recommend close).
5. #466 ADR draft dispatch (verify_pid / discover_all; orphan-freshness contract vs
   #253/#339 stated explicitly).
6. Doc hygiene: CLAUDE.md stale quartet note + `/srv` pointer (O-D#75);
   GROUND_PHYSICS dangling `ALIGNMENT_ROADMAP` link → file to operating-doctrine.
7. 64K load verification; then #416's 128K tuning bracket.

## Branch pile dispositions

See #468 §"Branch pile" — notably `worktree-agent-ab5eebe7fd6e3dd66` (triage-provenance
before reap) and `fix/403-config-load-fail-loud` (promote to PR — today's incident is
its best argument).

## Perf ground (measured this box, this day)

One full psutil scan with cmdline = 3.1–12.3 s (elastic; 10.8 ms/proc under Defender
RTP; enumeration alone 0.01 s). Cold /status pre-fix 31.3 s (5 scans) → post-#464
2 scans (~12.6 s, unmeasured live). MCP 12.3 s every call; CLI `server status` 12.6 s
per invocation. Regression dated: `ad37fdca` (1→3), `e2dc0241` (3→5, the jump),
`a6b8baa` (3 s TTL, ineffective), `0b756b8` (5→2). Fire map source:
`docs/perf/2026-08-24-fire-map.html` (published artifact "llauncher Fire Map").
