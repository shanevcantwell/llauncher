# Changelog

All notable changes to this project will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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

- **Remaining single-L env vars renamed (#151).** The rest of the
  `LAUNCHER_*` surface — the `core/settings.py` path/log/cache family
  plus the runner scripts' UI bind address — now carries the same
  leading `L` as the #138 agent family:

  | Old                       | New                        |
  | ------------------------- | -------------------------- |
  | `LAUNCHER_RUN_DIR`        | `LLAUNCHER_RUN_DIR`        |
  | `LAUNCHER_AUDIT_PATH`     | `LLAUNCHER_AUDIT_PATH`     |
  | `LAUNCHER_LOG_DIR`        | `LLAUNCHER_LOG_DIR`        |
  | `LAUNCHER_LOG_MAX_BYTES`  | `LLAUNCHER_LOG_MAX_BYTES`  |
  | `LAUNCHER_LOG_KEEP`       | `LLAUNCHER_LOG_KEEP`       |
  | `LAUNCHER_FOOTER_CACHE_S` | `LLAUNCHER_FOOTER_CACHE_S` |
  | `LAUNCHER_UI_HOST`        | `LLAUNCHER_UI_HOST`        |

  As with #138, there is **no runtime fallback shim** (pre-v1): a
  legacy single-L name set in the environment is silently ignored and
  the documented default applies. All of these vars default sensibly
  (`~/.llauncher/...` paths, loopback UI bind), so only deployments
  that explicitly override them — e.g. volume-mounted container state
  per ADR-008 — need to act.

  **Operator migration** — these vars live wherever *you* export them
  (shell profile, container env, a repo-local `.env`, or `agent.env`
  for the systemd/NSSM service). The one-liners below cover the two
  managed files; fix shell exports by hand:

  - **Linux / systemd:**

    ```sh
    sed -i 's/^LAUNCHER_/LLAUNCHER_/' "${XDG_CONFIG_HOME:-$HOME/.config}/llauncher/agent.env"
    [ -f .env ] && sed -i 's/^LAUNCHER_/LLAUNCHER_/' .env   # from the repo root, if you keep one
    sudo systemctl restart llauncher-agent
    ```

  - **Windows / NSSM:**

    ```powershell
    (Get-Content $env:USERPROFILE\.llauncher\agent.env) `
      -replace '^LAUNCHER_', 'LLAUNCHER_' `
      | Set-Content $env:USERPROFILE\.llauncher\agent.env
    nssm restart llauncher-agent
    ```

  (The `^LAUNCHER_` anchor also covers any stragglers from the #138
  agent family in the same file.)
