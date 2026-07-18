# ADR-LLNCH-025: UI Endpoint-Layer Boundary, Enforced by a Static Test

**Status:** Accepted
**Date:** 2026-06-30
**Related:** ADR-LLNCH-008 (LauncherState stateless facade), ADR-LLNCH-010 (port ownership at the call site); `docs/ARCHITECTURE.md` (layer map + forbidden edges)

## Context

`ui/` is an **endpoint** layer (`docs/ARCHITECTURE.md`). The layering doctrine's
"one rule" is *dependencies point downward; siblings do not import siblings*.
For the UI that means two concrete obligations:

- Backend verbs go through the orchestration facades — `state` (the
  `LauncherState` facade, ADR-LLNCH-008) and `operations` (the stateless verbs).
- Remote-node I/O goes through `remote/` — `NodeRegistry` / `RemoteNode` /
  `RemoteAggregator` — which is the **single sanctioned HTTP client**. `remote`
  and `agent` are peers across the network boundary (`docs/ARCHITECTURE.md`);
  the UI is a `remote` *client*, never a node's HTTP caller and never the
  `agent` server's importer.

This was not a hypothetical risk. A past UI tab shipped a **cross-layer reach**
— a tab talking to a node directly instead of routing through the engine — and
the defect did not surface until *after* an alpha tag/release. Prose in
`architecture.md` named the rule, but nothing made a violation fail fast. The
bug class is structurally invisible to ordinary behavioral tests: a tab that
does its own HTTP still "works" against a live backend, so the breach only shows
up as a layering regression nobody is looking for until it bites in production.

## Decision

1. **Codify the UI boundary as a deterministic, AST-based test gate.**
   `tests/architecture/test_ui_layer_boundaries.py` statically scans every
   module under `llauncher/ui/**` and **fails** when a UI module imports:
   - a direct-HTTP transport — `httpx`, `requests`, `urllib`/`urllib3`,
     `http.client`, `socket`, `aiohttp`, `pycurl` (node I/O is `remote/`'s job);
   - a peer/sibling endpoint — `llauncher.agent.*`, `llauncher.mcp_server.*`, or
     `llauncher.cli` (sideways edges across the layer map).

   The failure message cites `docs/ARCHITECTURE.md` and the offending
   `file:line`, and points at the fix: *work through the engine/remote facade*.
   The test is self-checking — a companion meta-test feeds the classifier known-
   bad and known-good import shapes so a future refactor that neuters the scanner
   fails loudly instead of going silently blind.

2. **State the invariant in the layer map.** `docs/ARCHITECTURE.md` carries an
   explicit line: *`ui/` reaches the backend only through
   `state`/`operations`/`remote`; enforced by
   `tests/architecture/test_ui_layer_boundaries.py`.*

3. **Provide a headless UI test harness so the boundary is also exercised at
   runtime.** `tests/ui/conftest.py` offers a `streamlit.testing.v1.AppTest`
   fixture that drives a single tab with the engine facades mocked. A behavioral
   test (`tests/ui/test_nodes_tab.py`) asserts the UI reaches a node *only*
   through the `RemoteNode` facade and that **no raw socket escapes the UI**
   during render (`forbid_direct_http`) — the runtime complement to the static
   guard.

## Rationale

The static guard is the load-bearing piece: it is **procedural-first** (a
deterministic scan, not a judgment call), it runs in milliseconds in the normal
`pytest` invocation, and it catches the exact regression that escaped to the
alpha. Import-level detection is the right altitude — the bug class *is* an
illegitimate import edge, so checking imports catches it at the cheapest
possible point, before any behavior is even constructed.

### Positive Consequences

- The "UI tab reaches across a layer / hits a node URL directly" regression now
  fails on the introducing commit, in CI, with an actionable message.
- The rule stops being prose-only; it is executable and self-verifying.
- The AppTest harness unblocks incremental UI coverage (issue #69) on a shared,
  facade-mocking fixture, and demonstrates the boundary at runtime as well.

### Negative Consequences

- The HTTP-root and sibling-prefix lists are allow/deny lists that must be kept
  honest; a brand-new HTTP library imported in `ui/` would slip through until
  added. *Mitigation:* the set covers every transport in the repo's dependency
  surface today, and the meta-test documents the intent so additions are
  obvious. A genuinely new transport is a reviewable event. Whole-package roots
  (`httpx`, `requests`, `urllib3`, `socket`, `aiohttp`, `pycurl`) are banned
  outright; mixed stdlib namespaces (`http`, `urllib`) are *not* — only their
  transport submodules (`http.client`, `urllib.request`/`urllib.response`) are
  flagged, so `urllib.parse` / `http.HTTPStatus` and friends stay legal.
- **Dynamic imports are invisible to the AST scan.** `__import__("httpx")` and
  `importlib.import_module(...)` are `Call` nodes, not `Import`/`ImportFrom`, so
  the static guard does not see them. *Mitigation:* smuggling a transport into a
  UI tab via a string-built dynamic import is contrived and conspicuous in
  review, and the behavioral `forbid_direct_http` sentinel still fires on the
  actual socket connect/`connect_ex` regardless of how the library was imported.
- Static import analysis cannot catch a UI module that reaches the network via a
  *legitimately downward* dependency that itself misbehaves. *Mitigation:* that
  is a different defect (a `remote`/`core` bug), out of this guard's scope; the
  behavioral `forbid_direct_http` test narrows the runtime gap for the tabs it
  drives.

## Alternatives Considered

### Import-linter / a third-party architecture-fitness tool

A declarative contracts tool (e.g. `import-linter`) could express the same
edges. **Rejected for now:** it adds a dependency and a second config dialect to
keep in sync with `architecture.md`, for a rule small enough to express in ~40
lines of `ast` with a failure message that speaks the repo's own vocabulary
(facades, `remote/`, the alpha bug). If the number of layer contracts grows,
revisit adopting a dedicated tool.

### Behavioral tests only (no static guard)

Drive every tab through AppTest and assert no socket opens. **Rejected as the
primary mechanism:** behavioral coverage is necessarily partial (only the code
paths a test happens to exercise), whereas the bug class is a *static* property
of the source. The static scan is exhaustive over `ui/**` by construction; the
behavioral test is kept as a complement, not the catch.

## Open Questions

- [ ] **Re-baseline UI coverage.** `pyproject.toml` currently omits
  `llauncher/ui/*` from the coverage measurement (the non-UI floor is ≥93%, see
  `pytest.ini`). As the AppTest harness closes UI coverage, drop the omit and
  re-baseline the floor against the combined measurement. **Resolution:** tracked
  under issue #69's follow-on coverage phases; this ADR deliberately does *not*
  move the floor.
- [ ] **Generalize the guard to other endpoint layers?** The same scan shape
  could assert `agent`/`mcp_server`/`cli` boundaries. **Resolution:** revisit if
  a cross-layer reach is observed outside `ui/`; no invariant without a
  violation.

## Supersession Relationships

**Supersedes:** none — this codifies an existing prose rule, it does not change it.
**Superseded by:** TBD — if the repo adopts a dedicated architecture-fitness tool
for the full set of layer contracts, that ADR would supersede the bespoke scan.

## Implementation Notes

| File | Change | Description |
|------|--------|-------------|
| `tests/architecture/test_ui_layer_boundaries.py` | Created | AST scan of `llauncher/ui/**`; fails on direct-HTTP or peer/sibling imports; self-checking meta-test |
| `tests/ui/conftest.py` | Created | `AppTest` harness fixture (`tab_harness`), mocked-facade fixtures, `forbid_direct_http` |
| `tests/ui/test_nodes_tab.py` | Created | Add Node form rendered smoke (salvage #134 → #69 intent) + behavioral remote-I/O test |
| `.claude/architecture.md`, `docs/ARCHITECTURE.md` | Modified | Explicit "enforced by tests/architecture/test_ui_layer_boundaries.py" line |
