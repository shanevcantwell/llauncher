# Handoff — Multiuser migration + llauncher systemd (2026-06-25)

**Author:** Claude Code (host, user `claude`). **Companion to:** `BUGFIX_ROADMAP_2026-06-25.md` (same dir).
**Theme of the session:** getting the ecosystem **out of `/home/shane`** and onto a clean multiuser footing, and resolving how llauncher should run as a systemd-controlled service.

---

## TL;DR

1. Audited every `/home/shane` hardcode under `/srv/dev/`; filed **7 portability issues** across 6 repos.
2. Investigated llauncher logging; filed **3 logging bugs** (#192/#193/#195) and resolved the **systemd design (#191)** to **Option B1**.
3. Proposed a **multiuser group/user scheme** (`assistant` / `inference` / `llauncher`) — *pending operator ratification*.
4. Operator is concurrently doing the **privileged user/group + fine-grained `systemctl`** setup. Nothing on the box has been changed by me except the items in "Environment state" below.

---

## Decisions made (durable)

### llauncher systemd → **Option B1** (system unit + dedicated service account)
Full spec in **llauncher#191** (see the 2026-06-25 comment). Key points:
- System unit running as a dedicated **`llauncher`** account; `StateDirectory=` (/var/lib) + `LogsDirectory=` (/var/log) + journald for the manager. **Amends ADR-009** (which chose `systemd --user`).
- Only the per-config **child-stream logs** relocate; the agent's own log is *already* journald.
- B1 is viable now **because the migration already moved the executables to `/srv/dev`** (built in place → no redeploy tax).
- **Atomic installer is a hard requirement** (operator loses hours on manual-step context switches): one transactional `sudo scripts/systemd/install-system.sh`, rollback trap, idempotent.

### Multiuser group/user scheme (PROPOSED — needs ratification)
| Handle | Purpose | Members |
|---|---|---|
| `assistant` *(exists)* | edit `/srv/dev` source | shane, claude |
| `inference` *(new)* | drive+observe the model runtime | shane, claude, `llauncher` |
| `llauncher` *(new svc acct, nologin)* | run the daemon | (member of `inference`) |

Access model: `/srv/dev` = `2770 root:assistant` setgid+default-ACL; executables = ACL `u:llauncher:rX` (read/exec, never write); `/var/log/llauncher` = `2750 llauncher:inference` (service writes, operator tails); token in `/var/lib/llauncher` = `640 llauncher:inference`. **Principle:** group membership for symmetric peers, ACLs for asymmetric least-privilege; setgid+default-ACL on shared dirs (highest-leverage move for the whole migration).

---

## Issues filed this session

### `/home/shane` portability audit (7)
| Repo | Issue | What |
|---|---|---|
| langgraph-agentic-scaffold | #277 | gemini_webui adapter/schemas hardcodes |
| langgraph-agentic-scaffold | #278 | docker-compose host-path mount |
| semantic-kinematics-mcp | #34 | scripts + embeddings adapter hardcodes |
| thought-vault-integration | #35 | scripts + pyproject hardcodes |
| semantic-chunker | #5 | forensics script + embeddings adapter |
| ollama-shim | #1 | untrack committed `.claude/settings.local.json` |
| llauncher | #191 | relocate logs for systemd (now the B1 design issue) |

**Explicitly deferred (operator calls):** pi-jail (kept under `/home/shane` for now — can of worms), goan/goan-development (legacy), LAS beyond #277/#278.
**Recurring pattern worth a systemic fix:** duplicated `sentence_transformers_adapter.py` hardcode in *both* semantic-chunker and semantic-kinematics-mcp.

### llauncher logging (3)
| Issue | Disp. | What |
|---|---|---|
| #192 | bug | orphaned logs never reaped (no cross-file GC; delete/rename don't clean) |
| #193 | auto:draft | ADR-160 hash-in-filename orphans logs on rename/scheme-change |
| #195 | bug | `settings.py` probes `LLAMA_SERVER_PATH` at import → stale path bricks the package |

Ground truth (live Linux `~/.llauncher/logs`): 18 files, 0 rotations, ~5 confirmed old/new orphan pairs from the ADR-160 scheme change. **The ~4,000-file pile is the Windows agent (parked quartet), not Linux.**

---

## Migration state (facts, 2026-06-25)

- **llama.cpp** now builds in place at `/srv/dev/llama.cpp/` → binary `build/bin/llama-server` (+ `.so`s), `rwxrwxr-x shane:shane`. A `build.working/` sibling exists.
- **llauncher** code+venv at `/srv/dev/shanevcantwell/llauncher/` (the canonical clone). Its `.venv` **currently can't import** — `.env` still has `LLAMA_SERVER_PATH=/home/shane/github/llama.cpp/build/bin/llama-server` (dead path). **Repoint to `/srv/dev/llama.cpp/build/bin/llama-server`** (see #195).
- `/srv/dev` is `drwxrwx--- shane:shane` + ACL granting `assistant`. **No `inference` group or `llauncher` account exists yet.**
- GPU devices `/dev/nvidia*` are `crw-rw-rw-` (world-open — no group needed). All 61 `model_path`s are under `/mnt/storage/LLMs` (no $HOME wall).
- The **live agent** (pid was 7465 at session start) still runs as `shane` from the *old* `/home/shane/.../llauncher/.venv`, `systemd --user`, on `:8765` (binds 0.0.0.0, token auth via `X-Api-Key`).

---

## Open threads / next steps

1. **Ratify the group scheme** above → then I write the #191 ADR (amending ADR-009) + the atomic `install-system.sh`.
2. **Repoint `LLAMA_SERVER_PATH`** in the /srv/dev clone `.env` (and the eventual service `agent.env`) → `/srv/dev/llama.cpp/build/bin/llama-server`. Unblocks the venv (#195).
3. **Original task still pending:** swap `:8082` from `embeddinggemma-300M-F32-nonpooled` → `-pooled`. Blocked all session only because the agent token is shane-private. **The `inference` group (claude + token `640 llauncher:inference`) is the clean unblock** — then I can drive it over HTTP.
4. **Systemic fix candidate:** duplicated embeddings-adapter hardcode (sc + skm) — one pattern fixes both.
5. **Pre-commit guard** for `/home/[a-z]+/` in tracked files (stops the hardcode bleed going forward).

## Security / hygiene notes
- `~/.llauncher/agent.token` + `node_tokens.json` briefly sat world-readable (`0777`) in a `/srv/dev` copy during this session; that copy is being deleted. **Rotate the agent token** to be clean (regenerate in `agent.env` → restart). The token guards `:8765` on `0.0.0.0`.
- Operator noted a **backup-scheme gap**: host-local secrets/state aren't captured by any backup — parked for a separate context.

## Environment state (what I changed, user `claude`)
- `gh` authenticated as `shanevcantwell` (token at `/home/claude/.config/gh/hosts.yml`; scopes repo/workflow/gist/read:org); git credential helper → `gh auth git-credential` for HTTPS.
- git identity (global): `Shane V Cantwell <reflectiveattention@gmail.com>`.
- `git config --global safe.directory` entries added for the `/srv/dev/shanevcantwell/*` repos (shane-owned, cross-user).
- Retained read-only snapshot at `/tmp/llauncher-home-snapshot/` (mode 700): `config.json` (61 configs), `config-original.json` (11-config backup), `audit.jsonl`, `nodes.json` (token field redacted), `logs.listing.txt`. **No secret/token files retained.**
