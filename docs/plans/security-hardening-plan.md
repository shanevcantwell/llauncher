# Security Hardening Plan (Issue #70)

Status: **partial landing**. C1 + C2 shipped in PR #75 (merge `ec98026`, 2026-05-19); §6 tickets filed as #78–#87. Companion to `test-coverage-plan.md` (Phase C of that plan hosts the security-regression assertions enumerated in §4).

---

## 1. Threat model

### 1.1 Assumed deployment

llauncher's reference deployment is a hobbyist/researcher workstation cluster on a home LAN:

- **Primary host** — RTX 8000 workstation running the Streamlit UI, FastAPI agent, and MCP-stdio server.
- **Remote node** — RTX 3090 box at `192.168.137.1` running the FastAPI agent only, reached over LAN.
- Occasional Tailscale exposure (the operator's own devices reaching the agent across networks).
- **Realistic accident**: misconfigured router or UPnP-happy device causes the LAN agent port to leak to WAN for some span of time before the operator notices.

### 1.2 Adversaries in scope

- **Unauthenticated LAN peer** — a guest on the WiFi, a smart-TV/IoT device on the same VLAN, a roommate's laptop.
- **Compromised browser tab on the same host** — drive-by JS that can reach `localhost:8501` (Streamlit) or `localhost:8765` (agent).
- **Malicious model config** — a `config.json` entry crafted to inject argv into `llama-server` or to escape paths.
- **Accidentally WAN-exposed agent** — port-forward / Tailscale ACL mistake; the threat surface is "any internet host for a window of hours".

### 1.3 Adversaries out of scope

- Nation-state or APT actors.
- Hostile co-tenant with shell access on the host OS (game over already — they can `kill -9` llama-server and read `~/.llauncher/` regardless).
- Supply-chain attacks on PyPI/uv (handled by dep-pinning, not this plan).
- Side-channel attacks on the GPU.

The driving principle: **proportionate to a workstation tool, not enterprise**. Anything that would require a secrets manager, an mTLS PKI, or a SIEM pipeline is explicitly out of scope.

---

## 2. Per-surface assessment

### 2.1 FastAPI HTTP API

**What exists today:**
- App constructed in `llauncher/agent/server.py:185-210` (`create_app`).
- Auth middleware in `llauncher/agent/middleware.py` — enforces `X-Api-Key` against `LAUNCHER_AGENT_TOKEN`, uses `hmac.compare_digest` (good), 401 vs 403 split, exempts `/health` `/docs` `/redoc` `/openapi.json`.
- **Auth is optional**: if `LAUNCHER_AGENT_TOKEN` is unset (`core/settings.py:62-65`), `AuthenticationMiddleware` is not registered (`server.py:203`) — the API is wide open.
- Default bind is `0.0.0.0:8765` (`agent/config.py:9-10`), so unset-token + default-host = fully exposed to LAN.
- No CORS middleware configured — relies on default browser same-origin enforcement.
- No request-size limit; no rate limiting.
- Startup logs warn on `0.0.0.0` without auth (`server.py:233-245`), but only warn — no refusal.

**Risk:** medium-high under the stated threat model. An unauth'd LAN peer (or a brief WAN exposure window) can drive `/start/{port}`, `/swap/{port}`, `/stop/{port}`, `/cancel/{port}`, and `DELETE /models/{name}` against any configured model. This is full process-control of the GPU resource and any model config on disk.

### 2.2 MCP stdio server

**What exists today:**
- Registered in `llauncher/mcp_server/server.py` over `mcp.server.stdio` (`stdio_server`).
- No auth — implicit trust on the parent process that spawned it via stdio pipe. The MCP transport is stdio only; there is no HTTP listener for MCP.
- All verbs (`start_server`, `stop_server`, `swap_server`, `delete_model`, `add_model`, `update_model_config`, …) route into the same `operations` package as the HTTP API.

**Risk:** low under the stated threat model. The trust boundary is "whoever launched the MCP child" — typically Claude Desktop / Claude Code, which the operator launched themselves. There is no network surface to attack. The risk reduces to "did the user vet the MCP client they're handing tools to" — which is upstream of llauncher.

### 2.3 Audit log

**What exists today:**
- `llauncher/core/audit_log.py:92-97` — open in append mode, single write per record, no fsync, no lock.
- Path defaults to `~/.llauncher/audit.jsonl` (`core/settings.py:77-80`); permissions inherit umask (typically world-readable).
- No rotation, no truncation, no integrity hash, no signing.
- Anyone with shell access can `rm` or `truncate` the file — this is explicitly accepted (ADR-008: "out of scope … tracked separately").

**Risk:** low. The threat model puts hostile-shell-on-host out of scope, and append-mode multi-writer races are bounded (single-line JSON, `O_APPEND` atomic up to PIPE_BUF). The realistic miss is **unbounded growth** under heavy reconcile churn, which is a reliability concern rather than a security one.

### 2.4 `LAUNCHER_AGENT_TOKEN`

**What exists today:**
- Optional env var (`core/settings.py:63`).
- When unset → no middleware installed → API is open.
- When set → middleware enforces `X-Api-Key` on every non-exempt path, with constant-time comparison.
- No bootstrapping helper — operator must generate and set it themselves; no default token file in `~/.llauncher/`.
- `RemoteNode` carries an `api_key` per-node (`remote/node.py:104-111`), but the registry is the only thing storing it; there is no per-node token rotation.

**Risk:** the default-open posture is the single highest-impact finding. A user who installs llauncher and runs the agent gets a fully unauthenticated control plane bound to all interfaces with only a log-line warning.

### 2.5 Subprocess argv construction (llama-server)

**What exists today:**
- `llauncher/core/process.py:79-174` (`build_command`) builds a `list[str]` argv. No `shell=True` anywhere (`process.py:231-236` uses `Popen(cmd, …)` with the list form).
- All numeric/enum fields flow through Pydantic typing (`models/config.py:29-62`) — integers, floats, and `Literal[...]` for things like `flash_attn`, `cache_type_k`.
- **String fields that reach argv verbatim**: `model_path`, `mmproj_path`, `reverse_prompt`, `extra_args`.
- `extra_args` is parsed with `shlex.split` (`process.py:171-172`). This **does not** invoke a shell — but it does let a malicious config inject arbitrary llama-server flags (e.g. `--alias`, `--api-key`, or future flags that read other files).
- `config.name` is sanitized for log-file naming via `re.sub(r'[^\w\-]', '_', …)` (`process.py:209`) and the path is `.resolve()`d into `LOG_DIR`. Good.
- `model_path` is validated by `field_validator` to be an existing path (`models/config.py:65-80`) — but does **not** restrict to a model-root prefix, so a config with `model_path=/etc/shadow` would pass the existence check (though llama-server would then error on parse).

**Risk:** low-medium. No shell injection (the `shell=True` boundary is clean). The real risk is "malicious config → unexpected llama-server flags via `extra_args`" or "config points at an arbitrary readable file as the model". Both require write access to `~/.llauncher/config.json` *or* the ability to drive `POST /start` with `add_model` first — which collapses back into the auth-token finding.

### 2.6 Model config files

**What exists today:**
- `llauncher/core/config.py:36` — `json.loads(CONFIG_PATH.read_text())`. Plain JSON, no YAML, no pickle. Safe parser.
- Each entry passes through `ModelConfig.from_dict_unvalidated` (`models/config.py:82-106`), which Pydantic-validates the schema (types, ranges, literals).
- Unknown fields are silently dropped (`from_dict_unvalidated` pops legacy keys; pydantic v2 default is `ignore` unless `extra="forbid"`).
- Path validation is skipped on load (`_skip_path_validation`) so a stale-path config doesn't brick the launcher. Re-validates on use.

**Risk:** low. JSON parsing has no deserialization-gadget surface. Pydantic schema is tight on numerics. Lingering gap: `extra="allow"` posture means a config can carry arbitrary keys that future code might pick up — defense-in-depth only, no current exploit.

### 2.7 Remote-node trust (ADR-009)

**What exists today:**
- Self-loop short-circuit (`remote/node.py:139-161`) routes local calls in-process, bypassing the network and auth.
- Remote calls go over plain HTTP (`base_url = http://…`, `node.py:117-119`). No TLS. No certificate pinning. Auth via `X-Api-Key` header only if the registry entry carries one.
- Token is stored alongside the node entry in the registry (file-on-disk).

**Risk:** medium. Plaintext token over LAN HTTP — anyone with passive on-path access (rogue WiFi AP, ARP-poisoner on the same VLAN, the cable modem itself) can lift the token. Replay is unconstrained (no nonce, no timestamp). For LAN-only this is acceptable per the threat model; for Tailscale it's actually fine because Tailscale provides the transport encryption. For "accidentally WAN-exposed" it's catastrophic.

### 2.8 Streamlit dashboard mutate paths

**What exists today:**
- `llauncher/ui/tabs/forms.py:15`, `tabs/nodes.py:152`, `tabs/model_card.py:*` — Streamlit forms and buttons that drive `RemoteNode` calls.
- Streamlit ships with built-in XSRF protection (`server.enableXsrfProtection = True` by default) and same-origin restrictions on `_stcore/*` endpoints. Mutate-path requests go from the Streamlit *server* (Python side) to the agent over its own HTTP client — the browser never speaks to the agent directly.
- Model names and other user-controlled strings are rendered via `st.markdown`, `st.write`, `st.text` — Streamlit does HTML-escape these by default unless `unsafe_allow_html=True`. Need to grep for `unsafe_allow_html` in a follow-up; not seen in the sampled files.
- The Streamlit server itself binds wherever the operator launches it; default `localhost`, but `streamlit run … --server.address 0.0.0.0` would expose it. No auth on the UI itself — assumed to be operator-only.

**Risk:** low-medium. The browser→Streamlit hop has XSRF protection. The Streamlit→agent hop carries the agent's token (if configured). The realistic threats are: (a) `unsafe_allow_html` injection via a malicious model name if such a code path exists, (b) someone exposing the Streamlit port itself.

---

## 3. Proportionate controls

| # | Surface | Control class | Recommended action |
|---|---|---|---|
| C1 | Agent HTTP auth | Code change | **Require `LAUNCHER_AGENT_TOKEN` to be set when binding to anything other than `127.0.0.1`/`::1`.** Refuse to start with a clear error message; auto-generate a token in `~/.llauncher/agent.token` on first run with no `LAUNCHER_AGENT_TOKEN` and print it once. Keep stdin-piped override available. **(LANDED — PR #75, `ec98026`)** |
| C2 | Agent default bind | Config hardening | Change `LAUNCHER_AGENT_HOST` default from `0.0.0.0` to `127.0.0.1`. Operator opts in to LAN exposure explicitly. Strictly a default change — keep 0.0.0.0 as a valid value. **(LANDED — PR #75, `ec98026`)** |
| C3 | HTTP request limits | Code change | Add a Starlette middleware capping body size (e.g. 1 MiB) — defense in depth against accidental large-payload bugs. Low effort, low risk. |
| C4 | CORS | Do nothing + document | Document that no CORS headers are emitted (so browsers cannot make cross-origin requests to the agent from arbitrary pages). Add a regression test asserting absence of `Access-Control-Allow-Origin` on responses. |
| C5 | MCP stdio | Do nothing + document | Threat model puts this out of scope. Add a single sentence to the README clarifying "MCP server trusts whatever invoked it via stdio". |
| C6 | Audit log integrity | Do nothing + document | Bounded growth is a reliability item, not security. Reference ADR-008's deferral; consider rotation in a separate ticket (#TBD reliability bucket, not this plan). |
| C7 | `extra_args` injection | Code change | Add an explicit deny-list (or accept-list) for `--api-key`, `--alias`, and any future llama-server flag we want to control at the llauncher boundary. Validate at config-save time so the error surfaces in the UI/CLI, not at start time. |
| C8 | `model_path` scope | Code change (small) | Add an optional `LAUNCHER_MODELS_ROOT` env var; when set, validate `Path(model_path).resolve()` is under it on save. Default unset = current behavior preserved. |
| C9 | Remote-node TLS | Architectural change | Defer. Document that operators relying on cross-host trust should use Tailscale (transport encryption) or co-locate. Filing this as a follow-up to scope what TLS would look like is fine; not in the immediate "vibed" bucket. |
| C10 | Remote-node token storage | Config hardening | Ensure registry file (`~/.llauncher/nodes.json` or similar — to be verified during implementation) is `chmod 0600`. Small, cheap. |
| C11 | Streamlit XSS | Code change | Grep for `unsafe_allow_html=True` and audit each call site; ensure any user-controlled string (model name, node name, path) is escaped. Add a regression test that an `<img onerror=...>` model name renders escaped. |
| C12 | Streamlit bind | Do nothing + document | Document recommended `--server.address 127.0.0.1` invocation in the README and any startup scripts. |

The bias is **C1 + C2 + C7 + C8 + C10 + C11** as the small-and-vibed bundle. C3, C4 are nice-to-have add-ons. C5, C6, C12 are documentation-only. C9 is a deferred architectural item.

---

## 4. Test hooks (feed Phase C of `test-coverage-plan.md`)

Concrete assertions future tests should encode. Each is a one-liner the integration harness can execute against the agent.

1. **C1-a**: `GET /status` with no `X-Api-Key` returns 401 when `LAUNCHER_AGENT_TOKEN` is set.
2. **C1-b**: `GET /status` with wrong `X-Api-Key` returns 403 (not 401) when token is set.
3. **C1-c**: `GET /health` returns 200 without `X-Api-Key` even when token is set (exempt path).
4. **C1-d**: Agent process refuses to start when binding to non-loopback host with no token configured (exit code non-zero, stderr mentions token requirement).
5. **C1-e**: Agent auto-generates `~/.llauncher/agent.token` on first run when no env var is set and host is loopback.
6. **C1-f** (#87, landed): `create_app(auth_token=None)` and `create_app(auth_token="")` raise `ValueError`; the no-auth sibling `create_app_unauthenticated()` is the only sanctioned no-auth construction path and is documented test-only.
7. **C2-a**: With no env overrides, agent binds to `127.0.0.1` (verified via `netstat`/socket introspection in test).
8. **C3-a**: `POST /start/{port}` with a 10 MiB request body returns 413.
9. **C4-a**: `OPTIONS /status` does not include `Access-Control-Allow-Origin` in response headers.
10. **C7-a**: `add_model` (any surface) with `extra_args="--api-key foo"` raises a validation error.
11. **C7-b**: `add_model` with `extra_args="--alias evil"` raises a validation error.
12. **C7-c**: `add_model` with benign extra args (`--log-disable`) succeeds.
13. **C8-a**: With `LAUNCHER_MODELS_ROOT=/srv/models`, `add_model` with `model_path=/etc/passwd` raises a validation error.
14. **C8-b**: With `LAUNCHER_MODELS_ROOT` unset, the historical behavior is preserved (any existing path accepted).
15. **C10-a**: After `node add`, the registry file has mode `0600`.
16. **C11-a**: A model added with name `"<script>alert(1)</script>"` renders as escaped text in the dashboard's model-card view (HTML-level assertion against the Streamlit-rendered page).
17. **timing**: Authentication failure response time has no observable secret-length dependence (smoke test that `hmac.compare_digest` is still in place).
18. **MCP**: MCP server `call_tool` with an invalid model name returns a structured error, not a stack trace (already implicit; codify it).

---

## 5. Open Questions

1. **Should `LAUNCHER_AGENT_TOKEN` be required by default?** This plan recommends "required when binding non-loopback, auto-generated when binding loopback". Phase C tests in `test-coverage-plan.md` will hit this either way — the integration harness must decide whether the default test fixture sets a token explicitly or relies on auto-generation. The test fixture should set a token explicitly so the auth-on path is the exercised default; auto-generation gets its own dedicated test (assertion C1-e).
2. **MCP HTTP transport — permanent caveat.** The C5 "do nothing + document" stance is conditional on MCP remaining stdio-only. If an HTTP (or any networked) MCP transport is ever introduced, the trust-the-launcher argument in §2.2 no longer holds and MCP must be re-assessed as a network surface on par with the FastAPI agent (auth, bind defaults, request limits). This is a standing design constraint on the MCP surface, not a follow-up task: a PR that adds networked MCP is the trigger to revisit §2.2 and §3/C5 in the same change.
3. **`LAUNCHER_MODELS_ROOT` scope — hard root, no soft mode.** The plan's standing position is hard validation on save (errors surface in UI) when `LAUNCHER_MODELS_ROOT` is set; absence of the env var means no enforcement, which is the migration path. A "soft warning" mode is explicitly out of scope: the C8 follow-up (#82) implements the hard form, and any future PR proposing a soft/warn variant must re-open this subsection in the same change.
4. **Streamlit dashboard auth — no in-app auth layer.** The plan's standing position is that the dashboard relies on operator-controlled bind (loopback by default per C12 / #85) rather than a basic-auth layer in the app itself. Adding in-app auth would re-scope the dashboard against the threat model in §1; any future PR proposing one must re-open §2.8 / §3 in the same change.
5. **Remote node TLS** — confirm "use Tailscale for cross-host" is an acceptable answer rather than building a TLS story. Plan assumes yes.
6. **`registry` storage location and current permissions** — implementation work for C10 needs to verify the actual file path used by `llauncher/remote/registry.py` and check its current mode. Plan does not pre-resolve this; the follow-up ticket should.

---

## 6. Suggested follow-up tickets

Filed 2026-05-19. Status reflects state at filing time; check each issue for current state.

1. ~~`security: require LAUNCHER_AGENT_TOKEN when binding non-loopback; auto-generate on loopback first run (C1)`~~ — **closed #76** (shipped in PR #75 / `ec98026`)
2. ~~`security: change default agent bind from 0.0.0.0 to 127.0.0.1 (C2)`~~ — **closed #77** (shipped in PR #75 / `ec98026`)
3. `security: cap agent HTTP request body size at 1 MiB (C3)` — open **#78**
4. `security: regression test asserting no CORS headers on agent responses (C4)` — open **#79**
5. `docs: clarify MCP stdio trust boundary in README (C5)` — open **#80**
6. `security: validate extra_args against a deny-list of llama-server flags llauncher controls (C7)` — open **#81**
7. `security: optional LAUNCHER_MODELS_ROOT enforcement for model_path on save (C8)` — open **#82**
8. `security: chmod 0600 on remote-node registry file (C10)` — open **#83**
9. `security: audit UI for unsafe_allow_html and add escaping regression test (C11)` — open **#84**
10. `docs: README guidance for Streamlit --server.address binding (C12)` — open **#85**
11. `security/architecture: scope TLS / mTLS story for cross-host remote nodes (C9, deferred design)` — open **#86**
12. `security: tighten create_app(auth_token=None) to prevent silent no-auth construction` — open **#87** (added post-PR-#75 from independent review of the C1+C2 implementation)
