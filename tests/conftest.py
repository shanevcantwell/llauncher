import logging
import os
import socket

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from llauncher.core.process import invalidate_process_scan_cache
from llauncher.state import LauncherState
from llauncher.models.config import ModelConfig


# ``real_model_health`` is declared in pytest.ini's markers= block (single
# source of truth per #318); no dynamic pytest_configure registration needed.


@pytest.fixture(autouse=True)
def _patch_model_health(request):
    """Patch ``check_model_health`` to always return valid in tests.

    Prevents small test temp-files from triggering the >1 MB health gate,
    which would break existing state/eviction tests that were written before
    ADR-LLNCH-005 was added.

    Patch target moved (issue #57): ``state.py`` no longer imports
    ``check_model_health``; the operations layer's preflight module is the
    single consumer (wrapped via :func:`default_model_health_check`). The
    target ``llauncher.operations.preflight.mh.check_model_health`` resolves
    via attribute traversal to the same *module object* as
    ``llauncher.core.model_health``, so the patch is applied to that module
    attribute. Reach is therefore:

    - **Reached:** any caller that does attribute access against the
      module each call, e.g. ``mh.check_model_health(...)`` after
      ``from llauncher.core import model_health as mh``, or a fresh
      ``from llauncher.core.model_health import check_model_health``
      executed inside a function body (the lookup hits the module dict
      every time).
    - **Not reached:** call sites that bound the function name at module
      import time (``from llauncher.core.model_health import check_model_health``
      at module top level) — those hold a direct reference to the
      original function object and bypass the patched attribute.

    Tests that need the real implementation
    (``test_adr_cross_cutting``, ``test_agent_models_health_api``) can opt
    out by adding ``@pytest.mark.real_model_health``.
    """
    if request.node.get_closest_marker("real_model_health"):
        yield
        return

    mock_result = MagicMock()
    mock_result.valid = True
    mock_result.exists = True
    mock_result.readable = True
    mock_result.size_bytes = 1024 * 1024 + 1
    mock_result.reason = None
    mock_result.last_modified = None

    with patch(
        "llauncher.operations.preflight.mh.check_model_health",
        return_value=mock_result,
    ):
        yield


@pytest.fixture(autouse=True)
def _deterministic_delegation(monkeypatch):
    """Force the #200 delegation gate to in-process for the whole suite.

    A real llauncher agent may be listening on ``LLAUNCHER_AGENT_PORT``
    (8765) on the developer/CI host. Without pinning the gate, the
    auto-detect health probe would find it and the MCP/UI launch tests
    (test_mcp_flows, test_self_swap, ...) would POST real start/stop verbs
    to that live agent — spawning real models and breaking isolation.

    Pinning ``LLAUNCHER_DELEGATE_TO_LOCAL_AGENT=0`` makes every front-end
    take the in-process path by default, matching the legacy behavior the
    bulk of the suite was written against. We also clear the
    ``LLAUNCHER_IS_AGENT_PROCESS`` stamp so no ambient value leaks in.
    Gate-specific tests override these via their own ``monkeypatch`` (which
    wins, being applied inside the test body after this autouse setup).
    """
    monkeypatch.setenv("LLAUNCHER_DELEGATE_TO_LOCAL_AGENT", "0")
    monkeypatch.delenv("LLAUNCHER_IS_AGENT_PROCESS", raising=False)


@pytest.fixture(autouse=True)
def _restore_root_logger_handlers():
    """Undo any ``logging.basicConfig(..., force=True)`` a test triggered.

    Issue #128: ``llauncher.agent.server.run_agent`` calls
    ``_configure_logging()``, which attaches a ``logging.FileHandler``
    (targeting whatever ``LAUNCHER_LOG_DIR`` resolved to at that moment,
    typically a per-test ``tmp_path``) to the *root* logger via
    ``basicConfig(force=True)``. The root logger is process-global state
    that outlives the test — a monkeypatch reverting ``LAUNCHER_LOG_DIR``
    afterwards does not detach or close that handler, so every
    subsequent test's log output (through any logger anywhere in the
    suite) keeps flowing into that now-defunct tmp_path file, and the
    open file descriptor itself leaks for the rest of the session.
    Snapshot-and-restore around each test contains the blast radius to
    the test that opened it.
    """
    root = logging.getLogger()
    before = list(root.handlers)
    before_level = root.level
    yield
    if root.handlers != before:
        for handler in root.handlers:
            if handler not in before:
                handler.close()
        root.handlers = before
        root.setLevel(before_level)


@pytest.fixture(autouse=True)
def _reset_process_scan_cache():
    """Purge the module-level process-scan TTL cache before every test.

    Issue #392: ``find_all_llama_servers`` / ``find_all_llama_servers_annotated``
    in ``llauncher.core.process`` are now cached (TTL=3s) to collapse
    redundant ``psutil.process_iter`` scans within one UI rerun. That cache
    is module-level state and persists across test invocations within the
    same process — without this reset, a mocked scan result from one test
    could leak into the next test's assertions (or a real-scan result
    could shadow a subsequent mock). Reset both before AND after so a
    populated cache never survives past this test either.
    """
    invalidate_process_scan_cache()
    yield
    invalidate_process_scan_cache()


@pytest.fixture(autouse=True)
def _isolate_nodes_file(tmp_path, monkeypatch):
    """Redirect the node-registry persistence file (+ its token sidecar) to
    a per-test tmp path.

    ``llauncher.remote.registry.NODES_FILE`` is a module-level Path pointing at
    ``~/.llauncher/nodes.json``. Several tests instantiate ``NodeRegistry()``
    without a per-fixture override and call ``add_node`` / ``remove_node``,
    which historically leaked test fixtures (``node1``, ``node2``, ``custom``,
    etc.) into the developer's real registry. This autouse fixture isolates
    every test by default; opt-out tests can monkeypatch back if needed.

    ``NODE_TOKENS_FILE`` (``~/.llauncher/node_tokens.json``, issue #132's
    sidecar for remote-node API keys — see ``registry.py``'s ``_save()``,
    which writes both files on every ``NodeRegistry`` persist) was
    discovered UNISOLATED during this fix's own #463 falsifier run: it is
    written unconditionally whenever ``_save()`` runs (even to just
    ``"{}"`` when there is no token data), so any test that persists a
    registry without patching this second path leaks the same way the
    #463 incident's anchors did — a second seam this fixture always should
    have covered. Fixed here rather than filed as a follow-up because it
    is the identical bug class this whole issue exists to close, and the
    fix is a one-line extension of an isolation fixture that already
    exists for the sibling file.
    """
    monkeypatch.setattr(
        "llauncher.remote.registry.NODES_FILE",
        tmp_path / "nodes.json",
    )
    monkeypatch.setattr(
        "llauncher.remote.registry.NODE_TOKENS_FILE",
        tmp_path / "node_tokens.json",
    )


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path, monkeypatch):
    """Redirect every ``LAUNCHER_STATE_DIR``-derived path onto a per-test tmpdir.

    Issue #463 incident: bare ``pytest`` (no env, no fixture request) wrote
    into the operator's real ``~/.llauncher`` — ``model_added``/``model_removed``
    audit lines and a live ``swap`` against the real 8081 agent. Root cause was
    two holes: (1) no suite-wide autouse isolation existed for
    ``LAUNCHER_STATE_DIR``/``CONFIG_DIR``/``LAUNCHER_AUDIT_PATH``/
    ``LAUNCHER_RUN_DIR`` — only opt-in fixtures (``mock_config_store``,
    ``mcp_env``) covered it, and a test that forgot to request one leaked; (2)
    the ``live`` marker was not deselected by default, so a real-port test ran
    under bare pytest too (see ``pytest_collection_modifyitems`` below).

    Two seams, deliberately BOTH:

    - **(a) env var** — ``LAUNCHER_STATE_DIR`` is read at import time by
      ``llauncher.core.settings`` (``settings.py:131-134``); setting it here
      propagates to any *subprocess* the test spawns fresh
      (``test_cli_state_dir.py``, ``test_state.py``, ``test_process.py`` all
      ``subprocess.run`` a new interpreter that re-imports settings from
      scratch and only ever sees the env, never an in-process patch).
    - **(b) module-attr patches** — the derived constants
      (``CONFIG_DIR``/``CONFIG_PATH``/``LAUNCHER_AUDIT_PATH``/
      ``LAUNCHER_RUN_DIR``/``LOG_DIR``) were already bound to the *old* value
      at each consuming module's import time, so setting the env var alone
      does not retro-redirect them for the in-process bulk of the suite —
      those constants must be monkeypatched directly. Attr set mirrors
      ``tests/integration/conftest.py``'s ``mcp_env`` (the reference
      isolation fixture, already proven correct).

    Neither seam alone is sufficient: env-only misses already-imported
    constants, patch-only misses subprocesses.

    ``#151`` rename coupling: this fixture and the socket guard below
    (``_forbid_live_ports``) are BOTH keyed on the literal env var name
    ``LAUNCHER_STATE_DIR`` (single-L; see ``settings.py`` module docstring —
    the state-dir family is single-L, the agent/stop-grace/metrics family is
    ``LLAUNCHER_``). If issue #151 ever re-spells this var, both sites must
    move together — this comment is the cross-reference so a rename PR's
    review gate sees the dependency.
    """
    state_dir = tmp_path / ".llauncher"
    run_dir = state_dir / "run"
    log_dir = state_dir / "logs"
    audit_path = state_dir / "audit.jsonl"
    config_path = state_dir / "config.json"

    # (a) env var — propagates to fresh-interpreter subprocesses.
    monkeypatch.setenv("LAUNCHER_STATE_DIR", str(state_dir))

    # (b) already-imported module attrs — the mcp_env reference set.
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_STATE_DIR", state_dir)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_RUN_DIR", run_dir)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_AUDIT_PATH", audit_path)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_LOG_DIR", log_dir)

    monkeypatch.setattr("llauncher.core.config.CONFIG_DIR", state_dir)
    monkeypatch.setattr("llauncher.core.config.CONFIG_PATH", config_path)

    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", audit_path)

    monkeypatch.setattr("llauncher.core.lockfile.LAUNCHER_RUN_DIR", run_dir)
    monkeypatch.setattr("llauncher.core.marker.LAUNCHER_RUN_DIR", run_dir)

    monkeypatch.setattr("llauncher.core.process.LOG_DIR", log_dir)


# The real, un-isolated state dir — resolved once at import time (matches
# ``settings.py``'s own default expression) so this guard checks the actual
# operator home directory regardless of what ``_isolate_state_dir`` patches
# for any given test.
_REAL_LAUNCHER_STATE_DIR = Path.home() / ".llauncher"
_REAL_AUDIT_PATH = _REAL_LAUNCHER_STATE_DIR / "audit.jsonl"
_REAL_CONFIG_PATH = _REAL_LAUNCHER_STATE_DIR / "config.json"
_REAL_NODES_PATH = _REAL_LAUNCHER_STATE_DIR / "nodes.json"
_REAL_NODE_TOKENS_PATH = _REAL_LAUNCHER_STATE_DIR / "node_tokens.json"

# Known test-fixture signatures that must never appear in a REAL, freshly
# appended audit line or the real config content. These are literal names
# used across the suite's synthetic models (see the #463 incident's anchor
# set — issue #463's ratification comment names this exact list):
# ``removable``/``keep-me`` (test_cli.py's remove-path tests),
# ``valid-model`` (test_config_validate_valid), ``nonexistent-model-xyz``
# (test_swap.py / test_self_swap.py's shared invalid-model literal), and
# this module's own ``isolation-test-model-463`` regression fixture name
# (tests/unit/test_isolation_fixtures.py).
_TEST_FIXTURE_SIGNATURES = (
    "removable",
    "keep-me",
    "valid-model",
    "nonexistent-model-xyz",
    "isolation-test-model-463",
)


def _real_audit_line_count() -> int:
    """Line count of the real, append-only audit ledger, or 0 if absent."""
    if not _REAL_AUDIT_PATH.exists():
        return 0
    return len(_REAL_AUDIT_PATH.read_text(encoding="utf-8").splitlines())


def _real_config_content() -> str:
    return _REAL_CONFIG_PATH.read_text(encoding="utf-8") if _REAL_CONFIG_PATH.exists() else ""


def _real_sidecar_existence() -> tuple[bool, bool]:
    """(nodes.json exists, node_tokens.json exists) on the real state dir.

    Discovered live during this fix's own falsifier run: ``registry.py``'s
    ``NodeRegistry._save()`` unconditionally writes BOTH ``NODES_FILE`` and
    its ``NODE_TOKENS_FILE`` sidecar (even an empty ``"{}"`` when there is
    no token data) whenever a registry persists. ``_isolate_nodes_file``
    (above) now patches both, but this is a lower-cost existence-only
    backstop (not content-signature matching like the audit/config checks)
    because on a fresh/simple single-node deployment (no remote nodes
    configured — this host's own state, verified during the incident
    investigation) NEITHER file exists at all until something creates
    them; a real operator remote-node action is comparatively rare
    (unlike the continuous audit-log/config activity Rider 2's
    false-positive mode is about), so "did a previously-absent sidecar
    just get created" is a low-false-positive, high-signal check here.
    """
    return _REAL_NODES_PATH.exists(), _REAL_NODE_TOKENS_PATH.exists()


@pytest.fixture(autouse=True)
def _forbid_real_state_writes():
    """Fail-closed: assert no TEST-ATTRIBUTABLE write landed in the real
    ``~/.llauncher`` ledger during this test.

    Issue #463's own falsifier, promoted to a per-test structural invariant.
    ``_isolate_state_dir`` above is the *positive* fix (redirect every write
    onto ``tmp_path``); this is the *negative* backstop — if some future
    change re-introduces a real-path write (a forgotten patch target, a new
    module capturing ``LAUNCHER_STATE_DIR`` at import time before the
    fixture list can reach it, a hardcoded ``Path.home()`` call bypassing
    ``settings`` entirely), this fixture fails *that* test by name instead
    of silently leaking into the operator's real audit log / config, the
    exact way the original incident went unnoticed.

    **Known false-positive mode, named per issue #463's ratification
    (Rider 2), option (a): content-based, not mtime-based.** A strict
    "mtime must not move" check false-positives whenever a legitimate
    writer is active concurrently with the suite — the real llauncher
    agent recording a real start/stop, or the operator driving the UI.
    This is not hypothetical: it happened during this fix's own falsifier
    run (the operator started a real model on port 8081 mid-suite,
    appending a real ``caller="agent"``/``caller="ui"`` line). A guard that
    flags legitimate concurrent activity teaches the suite to ignore it,
    which is worse than no guard — so this fixture instead: (1) counts
    audit-log LINES (append-only, so a genuine leak is always a net
    increase) and inspects only the NEWLY APPENDED lines for one of the
    known test-fixture signatures (``_TEST_FIXTURE_SIGNATURES``) rather
    than failing on any append; (2) for ``config.json`` (a full-file
    rewrite, not append-only, so there is no "new tail" to diff) checks
    whether the signature set appears anywhere in the post-test content
    that wasn't there before — a real operator-added model name will not
    collide with these synthetic names. A real, concurrent, legitimate
    writer can freely append/rewrite during the suite without failing an
    unrelated test; only a write carrying this suite's own fixture data
    trips the guard.
    """
    audit_before_count = _real_audit_line_count()
    config_before = _real_config_content()
    sidecars_before = _real_sidecar_existence()
    yield
    audit_after_count = _real_audit_line_count()
    config_after = _real_config_content()
    sidecars_after = _real_sidecar_existence()

    if audit_after_count > audit_before_count:
        # Re-read only the newly appended slice, not the whole file twice.
        new_lines = _REAL_AUDIT_PATH.read_text(encoding="utf-8").splitlines()[
            audit_before_count:
        ]
        leaked = [
            line
            for line in new_lines
            if any(sig in line for sig in _TEST_FIXTURE_SIGNATURES)
        ]
        assert not leaked, (
            "test wrote TEST-FIXTURE-SIGNATURED line(s) to the REAL "
            f"~/.llauncher/audit.jsonl: {leaked!r}. This test bypassed the "
            "autouse _isolate_state_dir fixture — a real write leaked the "
            "way the #463 incident did. Find the write's real path source "
            "(module attr captured before isolation, or a Path.home() call "
            "that skips llauncher.core.settings) and route it through the "
            "isolated seam instead. (Note: a real, non-test line appended "
            "here concurrently — e.g. the operator's own agent/UI activity "
            "— is NOT what tripped this; only a line containing one of "
            f"{_TEST_FIXTURE_SIGNATURES!r} does.)"
        )

    if config_after != config_before:
        newly_present = [
            sig
            for sig in _TEST_FIXTURE_SIGNATURES
            if sig in config_after and sig not in config_before
        ]
        assert not newly_present, (
            "test wrote TEST-FIXTURE-SIGNATURED content to the REAL "
            f"~/.llauncher/config.json: {newly_present!r}. This test "
            "bypassed the autouse _isolate_state_dir fixture — route the "
            "write through the isolated seam instead. (A real, concurrent, "
            "non-test config rewrite by the operator is NOT what tripped "
            "this.)"
        )

    newly_created_sidecars = [
        name
        for name, before, after in (
            ("nodes.json", sidecars_before[0], sidecars_after[0]),
            ("node_tokens.json", sidecars_before[1], sidecars_after[1]),
        )
        if after and not before
    ]
    assert not newly_created_sidecars, (
        f"test created the REAL ~/.llauncher/{newly_created_sidecars!r} "
        "which did not exist before this test ran. This test bypassed the "
        "autouse _isolate_nodes_file fixture (NodeRegistry._save() writes "
        "both nodes.json and its node_tokens.json sidecar unconditionally) "
        "— route the write through the isolated seam instead."
    )


# Live ports the real launcher stack binds to on this host. Kept as a tuple
# of ints (not a set) so the assertion message below can render them in a
# stable, readable order.
#
# #151 rename coupling: this guard and ``_isolate_state_dir`` above are BOTH
# keyed on the literal env var name ``LAUNCHER_STATE_DIR`` — the fixture
# patches state-dir-derived paths, and this guard is the enforcement surface
# for "bare pytest never reaches a live port" that the isolated state dir
# implies (an isolated lockfile/config can't point ops.* at a real running
# server). If issue #151 ever re-spells ``LAUNCHER_STATE_DIR``, both this
# guard and the fixture must move together; a rename PR's review gate should
# treat them as one coupled pair, not two independent edits.
_FORBIDDEN_LIVE_PORTS = (8081, 8082, 8765)


def _port_of(address) -> int | None:
    if isinstance(address, tuple) and len(address) >= 2:
        try:
            return int(address[1])
        except (TypeError, ValueError):
            return None
    return None


@pytest.fixture(autouse=True)
def _forbid_live_ports(monkeypatch):
    """Block real socket connects to the operator's live model/agent ports.

    Hand-rolled rather than ``pytest-socket`` — mirrors the existing,
    already-proven pattern in ``tests/ui/conftest.py``'s
    ``_forbid_direct_http`` (lower dependency footprint; this repo already
    trusts the pattern for the UI layer). Only ``connect``/``connect_ex`` are
    wrapped (not ``bind``), so:

    - the FastAPI ``TestClient`` is unaffected — it drives the ASGI app
      in-process over an httpx transport, never a real socket;
    - ephemeral-port allocation (``tests/integration/test_self_swap.py``'s
      ``_free_port()``, which only ``bind()``s to port 0 then closes) is
      unaffected;
    - any real bind+connect the ``mcp_env``/``live_agent_socket`` fixtures do
      against a fixture-allocated ephemeral port is unaffected, because those
      ports are never in the forbidden set;
    - only a connect whose destination port is literally 8081, 8082, or 8765
      is blocked, regardless of host spelling (``localhost``, ``127.0.0.1``,
      ``0.0.0.0``) since the check is on the resolved port, not the address.

    **``LLAUNCHER_LIVE=1`` bypass.** The guard's own error message tells a
    test author to opt into the real port via ``@pytest.mark.live`` +
    ``LLAUNCHER_LIVE=1`` (the ``pytest_collection_modifyitems`` opt-in below)
    — but that hook only controls *collection* (whether the test runs at
    all), not this *runtime* socket guard. Without an explicit bypass here,
    a test that legitimately opts into the live lane (``test_swap.py``'s
    ``TestSwapServerLive``, run with ``LLAUNCHER_LIVE=1``) would still be
    blocked the instant it tried to actually reach port 8081 — silently
    defeating the opt-in the plan's own risk section requires ("tests that
    legitimately need the real agent/port... must land behind the Phase-3
    env gate, not be broken"). So this fixture is a no-op entirely when
    ``LLAUNCHER_LIVE=1``, mirroring the collection hook's own gate exactly
    (same env var, same value) so "opted into live" means the same thing at
    both collection time and runtime.
    """
    if os.environ.get("LLAUNCHER_LIVE") == "1":
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _make_guarded(real_method, method_name):
        def _guarded(self, address, *args, **kwargs):
            port = _port_of(address)
            if port in _FORBIDDEN_LIVE_PORTS:
                raise AssertionError(
                    f"test attempted a real socket {method_name} to port {port} "
                    f"(forbidden set: {_FORBIDDEN_LIVE_PORTS}) — bare pytest "
                    "must never reach a live llauncher-managed port. If this "
                    "test intentionally needs the real port, mark it "
                    "@pytest.mark.live and run with LLAUNCHER_LIVE=1 (see "
                    "pytest_collection_modifyitems in this file) — that env "
                    "var also disables this guard for the whole session."
                )
            return real_method(self, address, *args, **kwargs)

        return _guarded

    monkeypatch.setattr(
        socket.socket, "connect", _make_guarded(real_connect, "connect")
    )
    monkeypatch.setattr(
        socket.socket, "connect_ex", _make_guarded(real_connect_ex, "connect_ex")
    )


def pytest_collection_modifyitems(config, items):
    """Deselect ``@pytest.mark.live`` tests unless ``LLAUNCHER_LIVE=1``.

    Issue #463 Phase 3 (safe-default only — the full marker-taxonomy
    reconciliation with #320 is a bounced design decision, not absorbed
    here). Mirrors the repo's existing opt-in pattern: ``integration_real``
    -> ``LLAUNCHER_INTEGRATION_REAL=1`` (``tests/integration/conftest.py``),
    ``live_agent_socket`` -> ``LLAUNCHER_LIVE_AGENT_SOCKET=1``
    (``test_live_agent_auth_socket.py``). The ``live`` marker itself was the
    one gap: declared in ``pytest.ini`` but never deselected, so bare pytest
    collected and ran ``test_swap.py``'s ``TestSwapServerLive`` against the
    real port 8081 — one of the two root-cause holes behind the #463
    incident (the other is the isolation gap ``_isolate_state_dir`` above
    closes).

    Skip, not filter-out: skipped tests still appear in the report (as
    ``s``), preserving visibility that a live lane exists and how to opt in,
    rather than silently vanishing from collection.
    """
    if os.environ.get("LLAUNCHER_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(
        reason="live test skipped by default; set LLAUNCHER_LIVE=1 to opt in"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Temporary directory for config files."""
    return tmp_path / ".llauncher"

@pytest.fixture
def mock_config_store(tmp_config_dir):
    """Mock ConfigStore with temporary paths.

    Also redirects ``LAUNCHER_AUDIT_PATH`` so the CRUD audit entries
    added per issue #60 are written into ``tmp_path`` instead of the
    developer's real ``~/.llauncher/audit.jsonl``.
    """
    audit_target = tmp_config_dir / "audit.jsonl"
    with patch('llauncher.core.config.CONFIG_DIR', tmp_config_dir) as mock_dir, \
         patch('llauncher.core.config.CONFIG_PATH', tmp_config_dir / 'config.json') as mock_path, \
         patch('llauncher.core.audit_log.LAUNCHER_AUDIT_PATH', audit_target), \
         patch('llauncher.core.settings.LAUNCHER_AUDIT_PATH', audit_target):
        yield mock_dir, mock_path

@pytest.fixture
def sample_model_config():
    """Sample model configuration for tests."""
    # Use from_dict_unvalidated to bypass the path existence check during tests
    return ModelConfig.from_dict_unvalidated({
        "name": "test-model",
        "model_path": "/fake/path/model.gguf",
        "n_gpu_layers": 255,
        "ctx_size": 4096,
    })

@pytest.fixture
def launcher_state(mock_config_store):
    """LauncherState with mocked dependencies."""
    # Mock process management to avoid real side effects
    with patch('llauncher.core.process.find_all_llama_servers', return_value=[]):
        yield LauncherState()


@pytest.fixture
def fake_managed_pid(monkeypatch):
    """Register a lockfile whose pid resolves to a stubbed ``ServerProcessInfo``.

    Issue #466 Phase 1 risk (§7, "the #463 isolation-fixture interaction"):
    once a caller wires ``verify_pid`` onto a lockfile-claimed pid (Phase 2),
    a fixture that writes a lockfile for a pid nothing verifies would break
    confusingly under #463's tmpdir state-dir isolation — a fabricated pid
    fails liveness (``None``), and ``os.getpid()`` passes liveness but fails
    the llama-server identity check (also ``None``). Either way the failure
    reads as "server silently absent" rather than "test fixture incomplete."

    This fixture writes the lockfile AND registers the matching
    ``verify_pid`` stub in one call, landing in Phase 1 (per the ratified
    plan) so Phase 2 has it before it needs it. ``llauncher.core.process``
    is patched at the module-attribute level — the same seam
    ``test_agent.py``'s ``TestStatusScanDedup309`` docstring names as the
    one a future caller must reference by module attribute, not a bound
    by-name import, for a single patch point to reach it.

    Returns a callable
    ``register(port, model, pid, *, run_dir=None, alias=None,
    model_path=None, create_time=None, cmdline_unreadable=False) -> Lockfile``.
    Pids never passed to ``register`` fall through to the real ``verify_pid``.

    ``cmdline_unreadable=True`` expresses Phase 2's #208 case — a
    cross-uid pid that is present but whose argv this uid cannot read, so
    it must stay in the roster as unknown-alive rather than being dropped.
    On an ``expect_port`` mismatch the stub returns ``None`` *and* emits
    the same WARNING the real ``verify_pid`` does (ADR-008).
    """
    from llauncher.core import lockfile as lf
    from llauncher.core import process as proc_mod

    stubs: dict[int, proc_mod.ServerProcessInfo] = {}
    real_verify_pid = proc_mod.verify_pid

    def _fake_verify_pid(pid: int, *, expect_port: int | None = None):
        if pid in stubs:
            info = stubs[pid]
            if expect_port is not None and info.port != expect_port:
                # Mirror the real verify_pid's ADR-008 refusal log so a
                # test asserting on the warning behaves the same against
                # the stub as against the real process table.
                proc_mod.logger.warning(
                    "verify_pid: pid %s argv port %s does not match expected "
                    "port %s — refusing to treat this as the claimed server "
                    "(ADR-008)",
                    pid, info.port, expect_port,
                )
                return None
            return info
        return real_verify_pid(pid, expect_port=expect_port)

    monkeypatch.setattr(proc_mod, "verify_pid", _fake_verify_pid)

    def _register(
        port: int,
        model: str,
        pid: int,
        *,
        run_dir: Path | None = None,
        alias: str | None = None,
        model_path: str | None = None,
        create_time: float | None = None,
        cmdline_unreadable: bool = False,
    ) -> lf.Lockfile:
        lock = lf.write_lockfile(port, model, pid, run_dir=run_dir)
        stubs[pid] = proc_mod.ServerProcessInfo(
            pid=pid,
            port=port,
            alias=alias if alias is not None else model,
            model_path=model_path,
            create_time=create_time,
            cmdline_unreadable=cmdline_unreadable,
        )
        return lock

    return _register
