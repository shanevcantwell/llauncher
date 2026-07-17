# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

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
