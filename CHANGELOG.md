# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Documentation

- **UI auth restored end-to-end after the security-hardening cohort
  (#131, #132, #134).** Phase 0 of the cross-node auth provisioning
  roadmap (#137): the manual token-copy pairing flow is now documented
  — README "Adding a remote node" walkthrough with per-platform token
  paths, platform-specific Add Node form help text, and an info banner
  pointing at the planned trusted-host successor (ADR-017 draft, #135).
  Docs are explicit that the static token is a trusted-LAN convenience,
  not strong auth.

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
