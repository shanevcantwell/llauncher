# Architecture Decision Records (ADRs)

ADRs are filed in status subfolders. Status reflects the **implementation
state of the decision**, not the maturity of the ADR document itself.

| Folder | Meaning | Count |
|--------|---------|-------|
| [`completed/`](./completed/) | Accepted; implementation done; no open issues tracking gaps against the ADR | 10 |
| [`accepted/`](./accepted/) | Accepted; known partial implementation tracked as open issues, or scope explicitly deferred in the ADR's own §Deferred Work | 7 |
| [`superseded/`](./superseded/) | Replaced by a later ADR; preserved as historical record | 2 |
| [`draft/`](./draft/) | Not yet ratified | 1 |

ADR statuses inside the documents themselves follow the canon laid out
in `docs/v2-handoff.md` §Conventions:
`Draft` → `Accepted` → optionally `Superseded by ADR-NNN`.
Folder placement and in-document Status are kept in sync.

## Index

### Completed

- [ADR-001 — TypeScript Extension for Pi to Control llauncher Agents](./completed/001-ts-extension-for-pi.md)
- [ADR-003 — Authentication for Agent API (Port 8765)](./completed/003-agent-api-authentication.md)
- [ADR-005 — Model Cache Health Validation in Start/Stop Flow](./completed/005-model-cache-health.md)
- [ADR-007 — Repeat-Penalty Tuning](./completed/007-repeat-penalty-tuning.md)
- [ADR-010 — Port Ownership at the Call Site](./completed/010-port-ownership-at-call-site.md)
- [ADR-011 — Swap Semantics v2](./completed/011-swap-semantics-v2.md)
- [ADR-012 — Footer Context Endpoint — Minimal Payload, Short TTL Cache](./completed/012-footer-context-endpoint.md)
- [ADR-013 — Per-Server Log Lifecycle (Append, Rotate, Bounded Tail)](./completed/013-logs-lifecycle.md)
- [ADR-014 — Cancellation of In-Flight Start/Swap](./completed/014-cancellation.md)
- [ADR-016 — Canonical Self-Swap — Worked Example and Integration Test](./completed/016-canonical-self-swap.md)

### Accepted (with known partial implementation)

- [ADR-004 — CLI Subcommand Interface](./accepted/004-cli-subcommand-interface.md) — `swap`, `logs` subcommands deferred
- [ADR-006 — GPU Resource Monitoring and VRAM Tracking](./accepted/006-gpu-resource-monitoring.md) — `?full=true` filter + ROCm/MPS backends deferred; tracking #44
- [ADR-008 — LauncherState as Stateless Facade](./accepted/008-launcher-state-stateless-facade.md) — `state._start_with_eviction_impl` retained for eviction-API smoke contract; M5/M6 cleanup pending
- [ADR-015 — Orphan Policy (Annotation and Listing)](./accepted/015-orphan-policy.md) — `adopt` verb deferred per §Deferred Work
- [ADR-018 — llauncher as a System Service](./accepted/018-llauncher-system-service.md) — `--system` install mode landed (#194); host provisioning (#196) and `LAUNCHER_STATE_DIR` Python support (#197) tracked separately; supersedes ADR-009's deployment posture
- [ADR-LLNCH-019 — Server-Metrics Surface (Live In-Server Inference Telemetry)](./accepted/adr-llnch-019-server-metrics-surface.md) — ratified; implementation not yet begun; deferred scope tracked #174/#175/#176
- [ADR-022 — llauncher UI under Operator-Scoped `systemd --user` Control](./accepted/022-llauncher-ui-user-service.md) — per-operator user unit (`scripts/systemd/llauncher-ui.service.user.in` + `install-ui.sh`); narrows ADR-018's UI posture; `/usr/local/bin` symlink (`install-cli.sh`, root) and `inference`-group membership are operator/host steps (#223)

### Draft

- [ADR-017 — Trusted-Host Session-Token Issuance (Design B)](./draft/017-session-token-issuance.md) — Phase 1 of the provisioning roadmap (#135 / #137); supersedes the static-token-only framing of ADR-003 on ratification

### Pending (unmerged branch)

- **ADR-LLNCH-021 — Progress-Snapshot Operation** — drafted on branch `docs/adr-019-progress-snapshot` as `019`; **renumbered to `021`** to resolve the `019` collision with the accepted Server-Metrics Surface ADR (a distinct decision, not subsumed). `020` is earmarked for the deferred Streamlit monitor (#176). Apply the rename when the branch lands.

### Superseded

- [ADR-002 — Unified Swap-with-Eviction Semantics](./superseded/002-swap-eviction-consistency.md) — superseded by ADR-011
- [ADR-009 — Symmetric Hub/Spoke Topology](./superseded/009-symmetric-hub-spoke-topology.md) — deployment posture superseded by ADR-018 (topology decisions preserved)

## Moving an ADR between folders

When implementation work closes the last open gap against an `accepted/`
ADR, `git mv` it to `completed/` and update the row in the table above.
When an ADR is superseded, set its Status field to
`Superseded by ADR-NNN`, `git mv` it to `superseded/`, and add a
Supersession section to the new ADR pointing back.
