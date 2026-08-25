# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Recommended llama.cpp build recipe, Windows + Linux (#473).**
  `scripts/build-llama-server.sh` and `scripts/windows/build-llama-server.ps1`
  build llama-server with `CMAKE_BUILD_TYPE=Release` and
  `GGML_CUDA_FA_ALL_QUANTS=ON` verified by reading back `CMakeCache.txt`,
  guarding two previously-silent failure modes: a single-config generator
  silently ignoring `--config Release` (shipping an unoptimized Debug
  build), and flash-attention quant support defaulting off (prompt
  processing collapsing 20-40x with no error). Both scripts print the
  resulting `LLAMA_SERVER_PATH` on success.
- **Audit-log reads exposed via CLI and MCP (#338, #454).**
- **`llauncher server swap` subcommand (#337, #430).**
- **`scripts/ensure-server.sh`, an idempotent converge-a-model-onto-a-port
  script for login/cron/agent use (#424).**
- **Pinned `/opt/llauncher/venv` systemd units + compose ritual (#360, #362).**
- **`LLAUNCHER_UI_PORT` env var with fail-loud validation (#359).**
- **Model-config delete, wired through the CLI and UI (#276, #278).**
- **`ctx_size`/`parallel` are now carried in `start`/`swap` tool results (#269).**
- **Server-metrics surface, ADR-019 (#264).**
- **Global `--state-dir` CLI override (#215).**
- **`metrics` config field to enable `llama-server --metrics` (#261).**
- **Fail-loud UI shared-venv backstop under systemd (#228, #232).**
- **`ensure-venv` oneshot guarantees the agent's systemd-managed venv (#227, #230).**
- **UI runs under operator-scoped `systemd --user`, per ADR-022 (#225).**
- **Remote-node provisioning is now discoverable (#134).** The README's
  Multi-Node section gained an *Adding a remote node* walkthrough naming the
  token file on each platform (`~/.llauncher/agent.token` on Linux,
  `%USERPROFILE%\.llauncher\agent.token` on Windows) and the SSH/RDP →
  read → copy → paste steps. The UI's Add Node form gained an info banner and
  platform-specific API-key help text. A new `llauncher-agent print-token`
  subcommand resolves and prints the local agent token to stdout, so
  `ssh <box> llauncher-agent print-token` replaces file-archaeology. UI auth
  is restored end-to-end after the security-hardening cohort
  (#131, #132, #134). The manual copy itself is eliminated by session-token
  issuance in a later phase (#137).

### Changed

- **`verify_pid`/`discover_all`, pid-first process primitives — #466 Phase 1
  (#470).** `core/process.py` gains `verify_pid(pid)`, a single-handle,
  single-cmdline-read lookup (~11 ms measured) implementing the ADR-008
  reconciliation table, alongside the renamed `discover_all()` (formerly
  `find_all_llama_servers_annotated`) world-walk. This lands the primitives
  only — no request path has been rewired to use `verify_pid` yet, so there
  is **no user-visible latency change in this alpha**; that's Phase 2
  (tracked on #466, see Known issues below).
- Windows `LLAMA_SERVER_PATH` documented in the installer flow (#380, #457)
  and in `agent.env.example`.
- UI thin-client invariant (`session_state` is view-state only, never
  authoritative) stated in `docs/ARCHITECTURE.md` and pointed to from
  `CLAUDE.md` for edit-time reach (#410, #411).
- The 2026-07-17 handoff capsule's "the product's core purpose has never
  worked" framing corrected to attribute a Windows Streamlit surface defect
  as a surface defect, not a core one (#378, #406).
- README documents both UI run postures (#355, #358), Docker's
  volume-mountable state paths (#38, #272), and the versioning statement
  that architecture generation (vN) and semver (0.x) are independent axes
  (#216).
- `CLAUDE.md`'s coverage gate now points at `pytest.ini` instead of
  restating the floor inline (#460).
- ADR-003's exempt-paths narrowed to match the live middleware (#126, #218).
- Doctrine pointers repointed to the `operating-doctrine` repo (#277);
  `.claude/architecture.md` consolidated into `docs/ARCHITECTURE.md` (#280).
- ADR-022 (UI under `systemd --user`, superseding ADR-018's hand-launched
  posture) and ADR-023 (service-owned venv recomposition) accepted and
  documented.

### Fixed

- **`ConfigStore.load()` fails loud on unreadable or corrupt config (#403,
  #472).** Previously, a missing config, a permissions/OSError, and a
  corrupt (`JSONDecodeError`) config all collapsed to an empty registry via
  a bare `print()` — violating this repo's fail-loud/parse-at-the-door rule.
  A genuinely absent config is still non-fatal (first run), now with a
  logged warning naming the resolved config path; an unreadable or corrupt
  config now raises, with the resolved path in the message.
- **Duplicate process-table scans removed on cold `GET /status` (#309,
  #464).** The cold-path scan count drops from 5 to 2 (state construction's
  own refresh, plus the handler's), by removing a redundant
  post-construction re-scan in `routing.get_state()` and skipping the
  handler's own re-scan on the request that just built the state. Warm-path
  staleness is unchanged (still bounded by the existing 3 s scan-cache TTL).
- Lockfile-race rollback now routes through `stop_server_by_pid` (#415, #444).
- Malformed `BLACKLISTED_PORTS` entries fail loud instead of silently
  dropping (#450, #456).
- Installer seeds `agent.env` with required keys only, not the full
  template (#382, #453).
- Redundant `state.refresh()` removed from the UI's Models tab (#370, #452).
- Readiness poll fast-fails when the launched process has already exited
  (#368, #440).
- Streamlit dependency floor bumped to the tested 1.58.0 (#439).
- Every unhandled 500 in the agent now logs a full traceback (#404, #437).
- `LLAUNCHER_AGENT_NODE_NAME` uses a falsy-or fallback instead of dropping
  an intentionally-empty value (#367, #436).
- MCP `stop_server` contract aligned; `get_server_logs`' `lines` bound
  fixed (#369, #435).
- `-ctk`/`-ctv` short aliases registered in the managed-flag collision
  guard (#399, #434).
- `__version__` now derives from package metadata — a single mint instead
  of a hardcoded duplicate (#425, #432).
- Running-server identity is sourced from `--alias`, not `model_path`
  (#429).
- `model_registry`'s `last_modified` guarded against a bare `str` before
  calling `.strftime` (#347, #428).
- Installer polls the actual `/health` endpoint instead of only
  service-is-active (#420, #421, #426).
- UI's eviction-confirm dialog persists across reruns (#419); `_handle_start`'s
  error persists across its own `st.rerun()` (#417).
- Process scan cache invalidated intrinsically inside the spawn/terminate
  primitives, instead of relying on callers to remember (#414).
- `server start` waits for readiness before reporting success (#413).
- Local node normalized to IPv4 in the node registry (#385).
- Redundant process-table scans that stalled UI navigation eliminated
  (#392, #396).
- BOM stripped from `install.ps1`'s env writes and agent-token sources
  (#127, #395).
- Installer's `[ui]` extra collapsed into base dependencies (#375); NSSM
  resolved via a PATH → choco fallback chain (#352, #372); durable NSSM
  logging plus surfaced silent start-500s (#128, #308, #345); dedupe of
  same-pass legacy-key collisions with an accurate guard message (#298,
  #325); NSSM `Application` repointed on every `install.ps1` run (#314,
  #315); empty-string elements allowed in `MigrateEnvKeys` (#305);
  `install.ps1` de-Unicoded to pure ASCII, with a matching Linux ASCII
  guard (#300, #302).
- Agent token/env reads are CRLF/BOM-tolerant at the door (#310, #326).
- Uvicorn access/error log lines are timestamped (#307, #324).
- Per-entry validation failure in `NodeRegistry._load` scoped to that entry
  instead of failing the whole load (#274).
- CLI config-path printing soft-wraps so a long path never breaks
  mid-atom (#256, #271).
- Node `host`/`port`/`timeout` validated via a Pydantic `NodeConfig`
  (#27, #266).
- Dead, mislabeled `ModelConfig.np` field removed (#265).
- An all-`None` `free_vram_mb` preflight reading fails loud instead of
  reporting "0 MiB" (#262).
- Audit tab's filter selection now forwarded to the remote `read_audit`
  call (#118).
- `run.sh stop` no longer bootstraps a venv (#229); `run.sh install` made
  honest about what it does, with the global-install path surfaced in
  `--help` (#154, #219).
- `wait_for_server_ready`'s tuple unpacked correctly, making the eviction
  rollback path reachable (#259).
- `extra_args` flag collisions fail loud; `batch`/`parallel` family
  exposed (#156, #242).
- VRAM preflight check fails loud when GPU device data is unavailable
  (#150, #240).

### Breaking changes

- **Agent env-var family renamed (#138).** The four service-facing agent
  environment variables now carry the leading `L` from the project name
  `llauncher`:

  | Old                          | New                           |
  | ---------------------------- | ----------------------------- |
  | `LAUNCHER_AGENT_TOKEN`       | `LLAUNCHER_AGENT_TOKEN`       |
  | `LAUNCHER_AGENT_HOST`        | `LLAUNCHER_AGENT_HOST`        |
  | `LAUNCHER_AGENT_PORT`        | `LLAUNCHER_AGENT_PORT`        |
  | `LAUNCHER_AGENT_NODE_NAME`   | `LLAUNCHER_AGENT_NODE_NAME`   |

  No runtime fallback shim is provided (pre-v1). Existing systemd/NSSM
  deployments will fail auth on next agent restart until operators
  update the env file by hand.

  **Operator migration:**

  - **Linux / systemd** — edit `${XDG_CONFIG_HOME:-$HOME/.config}/llauncher/agent.env`
    (typically `~/.config/llauncher/agent.env`; run `echo "${XDG_CONFIG_HOME:-$HOME/.config}/llauncher/agent.env"`
    to confirm the path on hosts with a non-default `$XDG_CONFIG_HOME`):

    ```sh
    sed -i 's/^LAUNCHER_AGENT_/LLAUNCHER_AGENT_/' "${XDG_CONFIG_HOME:-$HOME/.config}/llauncher/agent.env"
    sudo systemctl restart llauncher-agent
    ```

  - **Windows / NSSM** — edit `%USERPROFILE%\.llauncher\agent.env`:

    ```powershell
    (Get-Content $env:USERPROFILE\.llauncher\agent.env) `
      -replace '^LAUNCHER_AGENT_', 'LLAUNCHER_AGENT_' `
      | Set-Content $env:USERPROFILE\.llauncher\agent.env
    nssm restart llauncher-agent
    ```

  Fresh `scripts/systemd/install.sh` and `scripts/windows/install.ps1`
  runs write the new names. Existing `agent.env` files are **not**
  auto-migrated.

- **Installer now enforces `requires-python >=3.11` at the door (#334,
  #433).** An install attempt on an older interpreter fails loud instead of
  proceeding into a partially-broken environment.
- **The `agent.env` mirror file is retired — a single live env source is
  read directly by both the agent and the UI processes (#284, #286).**
  Deployments that depended on the second copy of `agent.env` lose it;
  fresh `install.sh`/`install.ps1` runs write only the one file.
- **Pre-#139 `LAUNCHER_AGENT_*` env keys are migrated at the door on next
  install/restart, and a missing token now fails loud instead of silently
  running unauthenticated (#282).** Consistent with this repo's
  parse-at-the-door rule — no dual-shape env file is read going forward.

### Internal/test

- Test-suite isolation and Windows-seat gate work: a suite-wide
  `LAUNCHER_STATE_DIR` isolation hole that let bare `pytest` runs write into
  the operator's real `~/.llauncher` state (and run the `live` marker
  against a real port) is closed with autouse fixtures covering both the
  env-var seam and already-imported module constants, plus a fail-closed
  backstop against real-state writes (#463, #469). `TestStatusScanDedup309`
  and friends extend the same scan-count-spy pattern into a standing gate
  (#464).
- Coverage close-outs across the core/runtime, ops/gpu, interface
  (CLI/MCP/agent), and remote clusters, raising the floor to 99% (#243,
  #244, #245, #247, #248, #250, #251, #329, #351); UI `AppTest` coverage
  built out across the Dashboard, Model Registry, Models, add/edit-model,
  and Audit tabs plus the layer-boundary and dispatch-seam-parity guards
  (#69, #340, #341, #342, #343, #344, #346, #348, #349); the same suite's
  `patch()`-based stubs retired in favor of real `AppTest` (#328, #461).
- `TEST_SUITE_SUMMARY.md` drift gate added and repeatedly regenerated to
  match the tree as it grew (#124, #222, #403, #408, #431).
- Real `X-Api-Key` auth exercised over a real socket to a real agent,
  replacing a mocked equivalent (#317, #327).
- Twelve review-verified defensive branches pinned against regression
  (#458).

### Known issues

*(New subsection — this file hasn't carried one before; adding it now
because several dated, unresolved items are worth an operator's awareness
going into this alpha.)*

- **Windows service: `Restart-Service llauncher-agent` terminates the
  managed llama-server children the agent spawned (#480, filed 2026-08-25).**
  A service restart should not take down in-flight inference. Related:
  #422 (systemd `KillMode` default has the same shape on Linux), #366, #383.
- **55 environment/portability failures on Windows as of `cf442f5`
  (2026-08-25), in the six buckets of #364; CI-green requires Linux.**
  WSL's `bash` shadowing Git Bash accounts for ~20 of them plus cascades;
  the rest are POSIX-permission-bit asserts on NTFS, `Path.home()` under a
  cleared environment, git path-quoting, and `test_ui_syntax.py`.
- **`extra_args` entries containing `-ctk`/`-ctv` emit a repeated
  `UserWarning` on every config load (visible in the UI's console stream)
  until #477 lands** (dropping the `cache_type_k`/`cache_type_v` shadow
  fields in favor of `extra_args` carrying llama-server flags verbatim).
- **`llauncher server start` from a cp1252 console (Git-Bash on Windows)
  prints a traceback after a successful launch, until #471/PR #478 lands.**
  The launch itself succeeds (`GET /status` shows the process running); the
  traceback is `UnicodeEncodeError` from printing a `✓`/`✗` glyph the
  console can't encode, and reads as a phantom failure. PR #478 is open,
  not yet merged, as of this entry.
- **Cold `GET /status` still costs ≈2 process-table scans (~12.6 s
  no-load, measured on the Windows box) — #466 Phase 2 is pending.** Phase
  1 (#470, above) landed the `verify_pid`/`discover_all` primitives only;
  no request path has been rewired to the cheap pid-addressed lookup yet.
