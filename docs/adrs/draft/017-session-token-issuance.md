# ADR-017: Trusted-Host Session-Token Issuance (Design B)

**Status:** Draft
**Date:** 2026-06-10
**Tracking:** #135 (Phase 1 of the provisioning roadmap, #137). Target: v0.4.0 ("v3 alpha").
**Doctrine:** `design-docs/ecosystem-ground-physics/` — `PARSE-AT-THE-DOOR`; no-shim migration posture.

## Context

ADR-003 and the security-hardening cohort (Wave 1+2, v2-final) landed the
*enforcement* layer for agent authentication: loopback-default bind,
refuse-to-start non-loopback without `LLAUNCHER_AGENT_TOKEN`, `X-Api-Key`
middleware with `hmac.compare_digest`, audit-logged endpoints. The
*provisioning* layer stayed manual: an operator adding a remote node copies a
32-byte secret across boxes by hand. PR #133 fixed the mechanical gap (the UI
can now store and send remote tokens via `~/.llauncher/node_tokens.json`), but
the copy step itself remains the dominant UX friction in the dominant
deployment shape — a small set of boxes administered by one operator on a
trusted LAN (see #137 §Threat-model anchor).

This ADR ratifies **Design B** from the 2026-05-25 planning session
(`docs/handoffs/2026-05-25-ui-auth-fix-and-provisioning-roadmap.md`): agents
mint short-lived session tokens for clients connecting from
operator-whitelisted source addresses, so adding a remote node from a
trusted UI host becomes "type host and port."

### Design constraints

1. Static-token auth (ADR-003) remains intact as the fallback for any client
   outside the trusted range. This ADR supersedes only the *static-token-only*
   language of ADR-003, not the ADR itself.
2. Existing v0.3.x deployments must upgrade with **zero configuration change
   and zero new attack surface**. The feature is strictly opt-in.
3. The threat-model honesty must be loud (see below) so trusted-host issuance
   is never mistaken for mutual authentication.
4. **No backwards-compatibility shims** (ecosystem ground physics,
   `PARSE-AT-THE-DOOR`): persisted shapes migrate deterministically at the
   door, once, or fail loud. Dual-parse paths are not carried. Default-off
   *feature* posture (constraint 2) is security posture, not a shim, and is
   unaffected by this.

## Decision

Add a trusted-host session-token issuance path on top of the existing static
token machinery.

### Whitelist configuration — `llauncher/agent/config.py`

- New env var **`LLAUNCHER_AGENT_TRUSTED_HOSTS`**: comma-separated list parsed
  into `ipaddress.ip_network` objects. CIDR and IPv6 supported. Parse errors
  (including ambiguous inputs like `192.168.1.1/33`) **fail loud at startup**:
  the agent refuses to bind, matching the PR #75 posture for missing tokens.
- **Unset or empty ⇒ the feature is off.** `POST /session` returns 403 for
  every caller, *including loopback*. This resolves an ambiguity in #135's
  spec ("loopback always implicit" vs. "empty ⇒ 403 for everyone"): implicit
  loopback applies only when the whitelist is non-empty and the feature is
  therefore enabled. Loopback clients never need `/session` — they resolve the
  static token from `~/.llauncher/agent.token` — so disabling the endpoint
  outright preserves the no-new-attack-surface invariant for upgrades.
- When non-empty, loopback (`127.0.0.0/8`, `::1/128`) is implicitly appended.

*(Note: #135 predates the #151 env-var rename and uses the `LAUNCHER_AGENT_*`
prefix; this ADR uses the post-rename `LLAUNCHER_AGENT_*` family throughout.)*

### Issuance endpoint — `llauncher/agent/server.py`

- New route **`POST /session`**. Not behind `verify_token`; instead an
  explicit check of `request.client.host` against the parsed whitelist.
- Success returns `{"token": "<urlsafe>", "expires_at": "<iso8601 UTC>"}`.
- Every request — success and reject — is **audit-logged unconditionally**
  with source IP.
- `request.client.host` is wrong behind a reverse proxy. Trusted-host mode
  **requires direct client connections**; no `X-Forwarded-For` trust is
  implemented, deliberately (header trust is its own spoofing surface).
  Documented in README.

### Token store — `llauncher/agent/auth.py`

- New **`SessionTokenStore`**: in-process dict keyed by token, value
  `expires_at`. Expired entries swept on access — no background task.
- TTL configurable via **`LLAUNCHER_AGENT_SESSION_TTL_SECONDS`**, default
  **24h** — a deliberate midpoint: longer widens the stolen-token window,
  shorter makes the UI re-bootstrap frequently.
- In-process means **agent restart revokes all session tokens**. That is the
  rotation/revocation mechanism for this phase (see §Deferred Work).

### Middleware — `llauncher/agent/middleware.py`

- `AuthenticationMiddleware` accepts *either* the static
  `LLAUNCHER_AGENT_TOKEN` *or* an unexpired session token, both compared via
  `hmac.compare_digest`. 401/403 separation unchanged.

### Client bootstrap — `llauncher/remote/node.py`, `llauncher/remote/registry.py`

- New **`RemoteNode.bootstrap_session()`**: when `api_key` is `None` on first
  contact, attempt `POST /session`; on success persist `{token, expires_at}`
  through the registry's normal save path into `node_tokens.json` (mode 0600,
  C10 invariant preserved — creds never in `nodes.json`). On expiry,
  re-bootstrap silently. The bootstrap result is cached for the session
  lifetime so issuance happens once per UI start, not per poll.
- `node_tokens.json` carries **exactly one schema**: `{token, expires_at}`
  per node, with `expires_at: null` meaning a static (non-expiring) token.
  PR #133's bare-string entries are **migrated once, at first load** —
  rewritten in place to `{token, expires_at: null}` — after which the old
  shape is never parsed again. A missing file is the normal empty state; a
  corrupt or unrecognized entry **fails loud at load** rather than degrading
  to `api_key=None`. This deliberately revises #133's silent-degrade posture
  per `PARSE-AT-THE-DOOR`: never trust-and-degrade on an unknown shape.
- A manually supplied API key in the Add Node form remains an override; the
  bootstrap path only fires when no key is configured.

### End state

An operator on a UI box whose IP is inside `LLAUNCHER_AGENT_TRUSTED_HOSTS`
adds a remote node by typing host + port. No token copy. Static-token flow
unchanged for everyone else.

## Threat model — read before relying on this

This design protects what the static-token design protects (operator opt-in
for non-loopback bind, no public-internet exposure) **plus** removes manual
provisioning *within the trusted network*. It does **not** defend against:

- LAN-resident attackers who can spoof a trusted source IP (ARP spoofing etc.)
- Passive sniffers on the network path — there is still no TLS
- Coresident user-level processes on the agent host

Source-IP trust is a *convenience* boundary, not an *adversarial* one. The
adversarial answer is Phase 3 (mTLS / pubkey pinning, #86). For two-box
home/office deployments administered by one operator — the project's actual
deployment shape — this posture is the right fit, and the README must say all
of this where operators will read it.

## Alternatives considered

- **Status quo + documentation (Phase 0, #134).** Ships regardless; does not
  remove the copy step. Kept as the floor, not the answer.
- **Design D — out-of-band pairing CLI (#136).** Better UX for first-time and
  roaming clients (6-digit codes), but depends on this ADR's
  `SessionTokenStore` anyway and adds interactive-CLI surface. Sequenced as
  Phase 2, not skipped.
- **Design C — mTLS / pubkey pinning (#86).** The real answer for hostile
  networks; heavyweight for the trusted-LAN posture and unplannable in detail
  today. Deliberately deferred to Phase 3. If a hostile-network use case
  arrives, Phase 3 jumps the queue and this ADR's path remains the UX layer
  on top.
- **Trusting `X-Forwarded-For` to support reverse proxies.** Rejected:
  header-based source trust is trivially spoofable from exactly the positions
  this feature implicitly trusts. Direct connections only.

## Testing requirements

- Middleware: session token accepted; rejected after TTL; unknown rejected;
  static-token path regression-free.
- `/session`: 403 for non-whitelist IPs (even with empty body); 200 for
  whitelist IPs without static token; 200 for loopback **when the feature is
  enabled**; 403 for everyone — loopback included — when
  `LLAUNCHER_AGENT_TRUSTED_HOSTS` is unset/empty.
- CIDR parsing: typos and invalid masks rejected at startup; IPv6 matches;
  loopback implicit when enabled.
- Bootstrap: first ping with no `api_key` triggers `/session`; subsequent
  pings reuse the stored token; silent re-bootstrap on expiry.
- Migration: a #133-shape (bare-string) `node_tokens.json` is rewritten once
  to `{token, expires_at: null}` and round-trips thereafter; corrupt or
  unrecognized entries refuse load loudly; a missing file yields an empty
  store.
- Audit log records every `/session` request (success + reject) with source IP.

## Consequences

**Positive:**
- "Type host and port" provisioning inside the trusted range; the roadmap's
  Phase 1 exit criteria (#135) are met.
- Strictly opt-in; no change of any kind for upgrading v0.3.x deployments.
- `SessionTokenStore` is the substrate Phase 2's pairing CLI builds on.
- Single persisted schema for `node_tokens.json` — no dual-parse drift
  surface carried forward.

**Negative:**
- Two accepted credential classes in the middleware instead of one — auth
  reasoning now has a second path to audit.
- Source-IP trust invites misreading as real authentication; mitigated by
  loud documentation, not eliminated.
- Reverse-proxy deployments cannot use the feature.
- A corrupt `node_tokens.json` now fails loud at load instead of silently
  pinging unauthenticated — intended, but a behavior change from #133.

**Same-orbit cleanups bundled with this phase:**
- **#126** — ADR-003 exempt-paths drift vs. live middleware: resolve while
  the middleware is open.
- **#125** — `/node-info` self-loop short-circuit: cooperative, would remove
  the local-node auth path entirely; bundle if cheap.

## Deferred Work

- Token revocation / rotation UX — agent restart is the mechanism this phase.
- First-time provisioning from outside the trusted range (fresh laptop,
  coffee-shop network) — static-token fallback covers it manually; Phase 2
  (#136) eliminates the corner.
- mTLS / pubkey pinning — Phase 3 (#86).

## Supersession note

Supersedes the static-token-*only* framing of ADR-003 §Decision. ADR-003
remains in force for the static path and stays in `completed/`; on
ratification, add an Amendment Note there pointing here.
