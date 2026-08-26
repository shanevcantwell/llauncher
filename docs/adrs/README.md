# Architecture Decision Records (ADRs)

ADRs are filed in status subfolders. Status reflects the **implementation
state of the decision**, not the maturity of the ADR document itself.

| Folder | Meaning | Count |
|--------|---------|-------|
| [`completed/`](./completed/) | Accepted; implementation done; no open issues tracking gaps against the ADR | 10 |
| [`accepted/`](./accepted/) | Accepted; known partial implementation tracked as open issues, or scope explicitly deferred in the ADR's own §Deferred Work | 10 |
| [`superseded/`](./superseded/) | Replaced by a later ADR; preserved as historical record | 2 |
| [`draft/`](./draft/) | Not yet ratified | 3 |

ADR statuses inside the documents themselves follow the canon laid out
in `docs/v2-handoff.md` §Conventions:
`Draft` → `Accepted` → optionally `Superseded by ADR-NNN`.
Folder placement and in-document Status are kept in sync.

## Index

### Completed

- [ADR-LLNCH-001 — TypeScript Extension for Pi to Control llauncher Agents](./completed/adr-llnch-001-ts-extension-for-pi.md)
- [ADR-LLNCH-003 — Authentication for Agent API (Port 8765)](./completed/adr-llnch-003-agent-api-authentication.md)
- [ADR-LLNCH-005 — Model Cache Health Validation in Start/Stop Flow](./completed/adr-llnch-005-model-cache-health.md)
- [ADR-LLNCH-007 — Repeat-Penalty Tuning](./completed/adr-llnch-007-repeat-penalty-tuning.md)
- [ADR-LLNCH-010 — Port Ownership at the Call Site](./completed/adr-llnch-010-port-ownership-at-call-site.md)
- [ADR-LLNCH-011 — Swap Semantics v2](./completed/adr-llnch-011-swap-semantics-v2.md)
- [ADR-LLNCH-012 — Footer Context Endpoint — Minimal Payload, Short TTL Cache](./completed/adr-llnch-012-footer-context-endpoint.md)
- [ADR-LLNCH-013 — Per-Server Log Lifecycle (Append, Rotate, Bounded Tail)](./completed/adr-llnch-013-logs-lifecycle.md)
- [ADR-LLNCH-014 — Cancellation of In-Flight Start/Swap](./completed/adr-llnch-014-cancellation.md)
- [ADR-LLNCH-016 — Canonical Self-Swap — Worked Example and Integration Test](./completed/adr-llnch-016-canonical-self-swap.md)
- [ADR-LLNCH-027 — Model Validate — a Single Read-Only Validation Path Reused Everywhere](./completed/adr-llnch-027-model-validate-read-only-verb.md) — ratified and implemented end-to-end in the same PR (#475)

### Accepted (with known partial implementation)

- [ADR-LLNCH-004 — CLI Subcommand Interface](./accepted/adr-llnch-004-cli-subcommand-interface.md) — `swap`, `logs` subcommands deferred
- [ADR-LLNCH-006 — GPU Resource Monitoring and VRAM Tracking](./accepted/adr-llnch-006-gpu-resource-monitoring.md) — `?full=true` filter + ROCm/MPS backends deferred; tracking #44
- [ADR-LLNCH-008 — LauncherState as Stateless Facade](./accepted/adr-llnch-008-launcher-state-stateless-facade.md) — `state._start_with_eviction_impl` retained for eviction-API smoke contract; M5/M6 cleanup pending
- [ADR-LLNCH-015 — Orphan Policy (Annotation and Listing)](./accepted/adr-llnch-015-orphan-policy.md) — `adopt` verb deferred per §Deferred Work
- [ADR-LLNCH-018 — llauncher as a System Service](./accepted/adr-llnch-018-llauncher-system-service.md) — `--system` install mode landed (#194); host provisioning (#196) and `LAUNCHER_STATE_DIR` Python support (#197) tracked separately; supersedes ADR-LLNCH-009's deployment posture
- [ADR-LLNCH-019 — Server-Metrics Surface (Live In-Server Inference Telemetry)](./accepted/adr-llnch-019-server-metrics-surface.md) — ratified; implementation not yet begun; deferred scope tracked #174/#175/#176
- [ADR-LLNCH-022 — llauncher UI under Operator-Scoped `systemd --user` Control](./accepted/adr-llnch-022-llauncher-ui-user-service.md) — per-operator user unit (`scripts/systemd/llauncher-ui.service.user.in` + `install-ui.sh`); narrows ADR-LLNCH-018's UI posture; `/usr/local/bin` symlink (`install-cli.sh`, root) and `inference`-group membership are operator/host steps (#223)
- [ADR-LLNCH-023 — Service-Owned Venv Recomposition](./accepted/adr-llnch-023-service-owned-venv-recomposition.md) — re-couples each service's `ExecStart` venv reference to a same-scope recompose guarantee (root `*-ensure-venv` oneshot units; user UI fail-loud backstop); amends ADR-LLNCH-018 / ADR-LLNCH-022; OQ1 resolved as shared `/opt` venv (2026-06-28); Phases A/B implementation pending
- [ADR-LLNCH-025 — UI Endpoint-Layer Boundary, Enforced by a Static Test](./accepted/adr-llnch-025-ui-endpoint-layer-boundary-enforced-by-test.md) — codifies the `ui/` → `state`/`operations`/`remote` rule as an AST guard (`tests/architecture/test_ui_layer_boundaries.py`) + AppTest harness; the deterministic catch for the cross-layer reach that escaped to an alpha; OQ: drop the `ui/*` coverage omit and re-baseline the floor (tracked #69)
- [ADR-LLNCH-026 — `ModelConfig` Does Not Mirror llama-server's Argument Schema](./accepted/adr-llnch-026-config-does-not-mirror-llama-server-schema.md) — ratified and implemented end-to-end in the same PR (#477); moved from `draft/` to `accepted/` 2026-08-26 to match its own in-document Status (the ratification comment's point 6 filed it under `draft/`, against the folder canon above); amended 2026-08-26 by ADR-LLNCH-028 (owned column re-opened under a stated admission criterion; `extra_args`-verbatim decision unchanged)

### Draft

- [ADR-LLNCH-017 — Trusted-Host Session-Token Issuance (Design B)](./draft/adr-llnch-017-session-token-issuance.md) — Phase 1 of the provisioning roadmap (#135 / #137); supersedes the static-token-only framing of ADR-LLNCH-003 on ratification
- [ADR-LLNCH-024 — Declarative Render Matrix](./draft/adr-llnch-024-declarative-render-matrix.md) — declarative config→backend-argument render matrix; Status: Draft; amended 2026-08-25 by ADR-LLNCH-026 (Phase 2 withdrawn; Phases 1/3 re-scoped to the six-field owned column) and 2026-08-26 by ADR-LLNCH-028 (Phase 2 reinstated, re-scoped to six named flags)
- [ADR-LLNCH-028 — Typed Flags Are The Ones llauncher Reasons About](./draft/adr-llnch-028-typed-flags-are-the-ones-llauncher-reasons-about.md) — admission criterion for typed `ModelConfig` fields (gate / constrain / compute-from, or an operator-designated per-entry control) + the primitive-value clause (never a `Literal` over llama-server's vocabulary); amends ADR-LLNCH-026, reinstates ADR-LLNCH-024 Phase 2; tracker #467; decision B open at ratification

### Pending (unmerged branch)

- **ADR-LLNCH-021 — Progress-Snapshot Operation** — drafted on branch `docs/adr-019-progress-snapshot` as `019`; **renumbered to `021`** to resolve the `019` collision with the accepted Server-Metrics Surface ADR (a distinct decision, not subsumed). `020` is earmarked for the deferred Streamlit monitor (#176). Apply the rename when the branch lands.

### Superseded

- [ADR-LLNCH-002 — Unified Swap-with-Eviction Semantics](./superseded/adr-llnch-002-swap-eviction-consistency.md) — superseded by ADR-LLNCH-011
- [ADR-LLNCH-009 — Symmetric Hub/Spoke Topology](./superseded/adr-llnch-009-symmetric-hub-spoke-topology.md) — deployment posture superseded by ADR-LLNCH-018 (topology decisions preserved)

## Moving an ADR between folders

When implementation work closes the last open gap against an `accepted/`
ADR, `git mv` it to `completed/` and update the row in the table above.
When an ADR is superseded, set its Status field to
`Superseded by ADR-NNN`, `git mv` it to `superseded/`, and add a
Supersession section to the new ADR pointing back.
