# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.5.0-alpha] — unreleased

### Breaking

- **`LAUNCHER_*` → `LLAUNCHER_*` env-var rename ([#151](https://github.com/shanevcantwell/llauncher/issues/151)) — tag-gated, entry finalized when it lands.**
  This release's tag itself is gated on #151 landing (ProdM ratification,
  [#484](https://github.com/shanevcantwell/llauncher/issues/484)); the remaining
  single-`L` `settings.py` family (`LAUNCHER_STATE_DIR`/`RUN_DIR`/`AUDIT_PATH`/`LOG_DIR`)
  and the UI host var move to the double-`L` `LLAUNCHER_*` name. Placeholder pending
  the actual PR — do not release against this section until it is replaced with the
  real migration notes.
- **Agent env-var family renamed ([#138](https://github.com/shanevcantwell/llauncher/issues/138)).** The four service-facing agent
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

### Added

- **Remote-node provisioning is now discoverable ([#134](https://github.com/shanevcantwell/llauncher/issues/134)).** The README's
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

### Highlights

Curated from `git log v0.4.1-alpha..HEAD` — see the full history for the complete set.

- **Test isolation:** full pytest could drive the *live* agent and the operator's real
  `~/.llauncher`, stopping a running model mid-suite. Closed by scoping
  `LAUNCHER_STATE_DIR` across the whole suite
  ([#463](https://github.com/shanevcantwell/llauncher/issues/463),
  [#469](https://github.com/shanevcantwell/llauncher/pull/469)).
- **Process-verify performance:** cold `GET /status` deduplicated its redundant
  process-table scans ([#309](https://github.com/shanevcantwell/llauncher/issues/309),
  [#464](https://github.com/shanevcantwell/llauncher/pull/464)), and `verify_pid`/`discover_all`
  moved to PID-first lookup instead of a full system scan — Phase 1 of
  [#466](https://github.com/shanevcantwell/llauncher/issues/466)
  ([#470](https://github.com/shanevcantwell/llauncher/pull/470)).
- **Fail-loud config loading:** `ConfigStore.load()` now raises on an unreadable or
  corrupt `config.json` instead of silently degrading
  ([#403](https://github.com/shanevcantwell/llauncher/issues/403),
  [#472](https://github.com/shanevcantwell/llauncher/pull/472)).
- **Audit read parity:** audit-log reads are exposed via CLI (`llauncher audit`) and MCP
  (`read_audit`), matching the agent's existing `GET /audit`
  ([#338](https://github.com/shanevcantwell/llauncher/issues/338),
  [#454](https://github.com/shanevcantwell/llauncher/pull/454)).
- **ADR namespace migration:** all ADR handles renamed to the `ADR-LLNCH-*` namespace,
  including #475's ADR born directly as `ADR-LLNCH-027`
  ([#479](https://github.com/shanevcantwell/llauncher/pull/479)).
- **cp1252-safe CLI output:** `server start` no longer crashes with `UnicodeEncodeError`
  printing the ✓/✗ status glyph on a cp1252 console — the launch had already succeeded,
  so the crash read as a phantom failure
  ([#471](https://github.com/shanevcantwell/llauncher/issues/471),
  [#478](https://github.com/shanevcantwell/llauncher/pull/478)).
- **`model validate` verb:** a new read-only validation path (CLI, HTTP agent, MCP tool,
  UI registry badge) reports missing/invalid model weights without attempting a start.
  `GET /models/validate[/{name}]` replaces `GET /models/health[/{name}]` outright — no
  alias, per this repo's no-dual-shape rule. Recorded as ADR-LLNCH-027
  ([#475](https://github.com/shanevcantwell/llauncher/issues/475),
  [#481](https://github.com/shanevcantwell/llauncher/pull/481)).
- **Config-error banner:** a corrupt/unreadable config now surfaces as a Streamlit
  `st.error` banner instead of a raw traceback (follow-up to #472)
  ([#476](https://github.com/shanevcantwell/llauncher/issues/476),
  [#486](https://github.com/shanevcantwell/llauncher/pull/486)).
- **Log wall-clock anchor:** per-model log banners gained a UTC wall-clock anchor line
  (anchor half only — the issue stays open for the Windows log-sparsity half)
  ([#405](https://github.com/shanevcantwell/llauncher/issues/405),
  [#485](https://github.com/shanevcantwell/llauncher/pull/485)).
- **llama.cpp build scripts:** added a recommended build recipe for Windows and Linux
  ([#473](https://github.com/shanevcantwell/llauncher/pull/473)).
