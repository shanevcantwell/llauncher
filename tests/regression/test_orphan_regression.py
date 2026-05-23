"""Regression tests for ADR-015 orphan policy (issue #55).

The existing ``tests/unit/test_orphan.py`` already covers the per-process
classification rules (managed, no-lockfile, stale, pid-mismatch,
unreadable, sort order) and the in-memory state dedupe behavior via
mocks. This file pins down the *cross-surface contract* and the
*write-side guarantees* that a refactor could silently break without
any unit test failing:

* **Annotation-only contract** (``llauncher/operations/orphan.py`` lines
  47-106): ``list_orphans`` reads ``lf.read_lockfile`` and
  ``lf.is_pid_alive`` only — it must NOT call any kill verb
  (``stop_server_by_pid`` / ``stop_server_by_port``) and must NOT write
  a lockfile claiming the orphan. ADR-015 §6 explicitly defers adoption
  ("Any lockfile *write* on behalf of an orphan pid" is out of scope).
  A refactor that tries to "fix" an orphan by reaping or claiming would
  silently violate the ADR.

* **Canonical envelope shape across surfaces** (HTTP/MCP/CLI): the same
  orphan must serialize identically on ``GET /orphans``, MCP
  ``list_orphans``, and ``llauncher orphan list --json`` — all three are
  documented in ADR-015 §5 as returning the same canonical fields
  (``pid``, ``port``, ``cmdline_unreadable``). A surface that drifts
  (drops a field, renames one, returns a different envelope) breaks
  the operator's "same data everywhere" promise.

* **Empty list is success, not 404 / error**: ADR-015 §5 specifies
  ``total: 0`` with an empty list. A surface that 404s on "no orphans"
  forces every caller to write a presence check; the regression here
  pins each surface to the success-with-empty-shape contract.

* **Audit-file idempotency** (``state.py`` lines 161-170 +
  ``orphan.py:record_observed_orphan`` lines 109-125): the existing
  unit test verifies the in-memory dedupe via mocking out
  ``record_observed_orphan``. This regression exercises the *actual*
  audit append path against a real tmp file to confirm that
  ``refresh_orphans`` writes exactly one ``OBSERVED_ORPHAN`` entry per
  pid-sighting-pair-per-agent-lifetime. A refactor that moves the
  dedupe check below the ``record_observed_orphan`` call (or removes
  it) would write a duplicate line on every refresh.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llauncher import operations as ops
from llauncher.core import audit_log as al
from llauncher.core import lockfile as lf
from llauncher.core.audit_log import AuditAction, AuditResult
from llauncher.operations.orphan import OrphanInfo


# ---------------------------------------------------------------------------
# Shared fixtures (mirroring the style of test_cancel_regression.py so this
# file can be run in isolation: ``pytest tests/regression``)
# ---------------------------------------------------------------------------


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "run"
    target.mkdir()
    monkeypatch.setattr("llauncher.core.lockfile.LAUNCHER_RUN_DIR", target)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_RUN_DIR", target)
    return target


@pytest.fixture
def audit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "audit.jsonl"
    monkeypatch.setattr("llauncher.core.audit_log.LAUNCHER_AUDIT_PATH", target)
    monkeypatch.setattr("llauncher.core.settings.LAUNCHER_AUDIT_PATH", target)
    return target


def _fake_proc(pid: int) -> MagicMock:
    m = MagicMock()
    m.pid = pid
    return m


# ---------------------------------------------------------------------------
# Annotation-only contract: list_orphans is observation, not reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case,setup",
    [
        ("no_lockfile", "no_lockfile"),
        ("stale_lockfile", "stale_lockfile"),
        ("pid_mismatch", "pid_mismatch"),
        ("unreadable_cmdline", "unreadable_cmdline"),
    ],
)
def test_list_orphans_never_kills_or_claims(
    case: str, setup: str, run_dir: Path
) -> None:
    """Pin ADR-015 §6: ``list_orphans`` annotates only — no kill, no claim.

    Covers ``llauncher/operations/orphan.py:47-106``. Every classification
    branch must read the lockfile and process table; none may call into
    ``proc.stop_server_by_pid`` / ``proc.stop_server_by_port`` (would
    reap an orphan we don't own) nor ``lf.write_lockfile`` (would
    silently adopt — adoption is deferred to a future ADR).
    """
    if setup == "no_lockfile":
        annotated = [(_fake_proc(9001), 8081, False)]
        is_alive = True
    elif setup == "stale_lockfile":
        lf.write_lockfile(8082, "old", 7777, run_dir=run_dir)
        annotated = [(_fake_proc(9002), 8082, False)]
        is_alive = False
    elif setup == "pid_mismatch":
        lf.write_lockfile(8083, "old", 6666, run_dir=run_dir)
        annotated = [(_fake_proc(9003), 8083, False)]
        is_alive = True
    elif setup == "unreadable_cmdline":
        annotated = [(_fake_proc(9004), None, True)]
        is_alive = True
    else:  # pragma: no cover - guarded by parametrize
        pytest.fail(f"unknown setup {setup!r}")

    with patch(
        "llauncher.operations.orphan.proc.find_all_llama_servers_annotated",
        return_value=annotated,
    ), patch(
        "llauncher.operations.orphan.lf.is_pid_alive", return_value=is_alive
    ), patch(
        "llauncher.core.process.stop_server_by_pid"
    ) as kill_pid, patch(
        "llauncher.core.process.stop_server_by_port"
    ) as kill_port, patch(
        "llauncher.core.lockfile.write_lockfile"
    ) as write_lock:
        result = ops.list_orphans()

    assert len(result) == 1, f"case {case}: expected exactly one orphan"
    kill_pid.assert_not_called()
    kill_port.assert_not_called()
    write_lock.assert_not_called()


# ---------------------------------------------------------------------------
# Cross-surface canonical envelope shape (ADR-015 §5)
# ---------------------------------------------------------------------------


_CANONICAL_FIELDS = {"pid", "port", "cmdline_unreadable"}


@pytest.fixture
def http_client():
    from fastapi.testclient import TestClient
    from llauncher.agent.server import create_app_unauthenticated
    from llauncher.agent import routing

    routing._state = None
    yield TestClient(create_app_unauthenticated())
    routing._state = None


def test_http_orphans_envelope_canonical(http_client, monkeypatch) -> None:
    """``GET /orphans`` returns ``{node, orphans:[...], total}`` per ADR-015 §5.

    Pins ``llauncher/agent/routing.py:196-219``. A surface drift here
    (renamed ``total`` to ``count``, dropped ``node``, wrapped in
    another envelope) would break MCP-vs-HTTP parity.
    """
    from llauncher.agent import routing

    fake_state = MagicMock()
    fake_state.orphans = [
        OrphanInfo(pid=11001, port=8081),
        OrphanInfo(pid=11002, port=None, cmdline_unreadable=True),
    ]
    monkeypatch.setattr(routing, "get_state", lambda: fake_state)

    resp = http_client.get("/orphans")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"node", "orphans", "total"}
    assert body["total"] == 2
    for o in body["orphans"]:
        assert set(o.keys()) == _CANONICAL_FIELDS


@pytest.mark.asyncio
async def test_mcp_list_orphans_envelope_canonical() -> None:
    """MCP ``list_orphans`` returns the same orphan dicts as HTTP.

    Pins ``llauncher/mcp_server/tools/servers.py:274-284``. The MCP
    envelope intentionally omits ``node`` (per-call MCP context),
    but each orphan dict MUST carry the canonical three fields.
    """
    from llauncher.mcp_server.tools.servers import list_orphans as mcp_list

    state = MagicMock()
    state.orphans = [
        OrphanInfo(pid=11003, port=8082),
        OrphanInfo(pid=11004, port=None, cmdline_unreadable=True),
    ]

    result = await mcp_list(state, {})
    assert set(result.keys()) == {"orphans", "total"}
    assert result["total"] == 2
    for o in result["orphans"]:
        assert set(o.keys()) == _CANONICAL_FIELDS


def test_cli_orphan_list_json_envelope_canonical() -> None:
    """``llauncher orphan list --json`` emits the same per-orphan dicts.

    Pins ``llauncher/cli.py:260-289``. CLI ``--json`` is the
    machine-readable surface; the table is for humans. The JSON form
    MUST be a flat list of canonical orphan dicts (no envelope) so it
    composes with ``jq`` the same way HTTP does after a ``.orphans``
    selector.
    """
    from typer.testing import CliRunner
    from llauncher.cli import app

    runner = CliRunner()
    orphans = [
        OrphanInfo(pid=11005, port=8083),
        OrphanInfo(pid=11006, port=None, cmdline_unreadable=True),
    ]
    with patch("llauncher.operations.list_orphans", return_value=orphans):
        result = runner.invoke(app, ["orphan", "list", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert isinstance(payload, list) and len(payload) == 2
    for o in payload:
        assert set(o.keys()) == _CANONICAL_FIELDS


def test_cross_surface_orphan_dicts_agree(http_client, monkeypatch) -> None:
    """The same ``OrphanInfo`` serializes identically across HTTP / MCP / CLI.

    Operators paste the JSON from one surface into a script that
    consumes another. The contract that "an orphan is an orphan
    everywhere" is what makes that work; this test pins it.
    """
    import asyncio

    from llauncher.agent import routing
    from llauncher.mcp_server.tools.servers import list_orphans as mcp_list
    from typer.testing import CliRunner
    from llauncher.cli import app

    orphans = [
        OrphanInfo(pid=12001, port=8084),
        OrphanInfo(pid=12002, port=None, cmdline_unreadable=True),
    ]

    # HTTP
    fake_state = MagicMock()
    fake_state.orphans = list(orphans)
    monkeypatch.setattr(routing, "get_state", lambda: fake_state)
    http_orphans = http_client.get("/orphans").json()["orphans"]

    # MCP
    mcp_state = MagicMock()
    mcp_state.orphans = list(orphans)
    mcp_result = asyncio.get_event_loop().run_until_complete(
        mcp_list(mcp_state, {})
    )
    mcp_orphans = mcp_result["orphans"]

    # CLI
    runner = CliRunner()
    with patch("llauncher.operations.list_orphans", return_value=list(orphans)):
        cli_result = runner.invoke(app, ["orphan", "list", "--json"])
    cli_orphans = json.loads(cli_result.stdout)

    # The three surfaces must agree on each orphan dict.
    assert http_orphans == mcp_orphans == cli_orphans


# ---------------------------------------------------------------------------
# Empty orphan list is a successful response across surfaces
# ---------------------------------------------------------------------------


def test_http_orphans_empty_is_200(http_client, monkeypatch) -> None:
    """Empty orphan list returns 200 with shape — not 404 / error.

    Pins ADR-015 §5: callers should not need a presence check.
    """
    from llauncher.agent import routing

    fake_state = MagicMock()
    fake_state.orphans = []
    monkeypatch.setattr(routing, "get_state", lambda: fake_state)

    resp = http_client.get("/orphans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["orphans"] == []
    assert body["total"] == 0
    assert "node" in body


@pytest.mark.asyncio
async def test_mcp_list_orphans_empty_returns_envelope() -> None:
    """MCP ``list_orphans`` with zero orphans returns the shape, not None / error."""
    from llauncher.mcp_server.tools.servers import list_orphans as mcp_list

    state = MagicMock()
    state.orphans = []

    result = await mcp_list(state, {})
    assert result == {"orphans": [], "total": 0}


def test_cli_orphan_list_empty_exit_zero() -> None:
    """``llauncher orphan list`` with zero orphans exits 0 (not an error)."""
    from typer.testing import CliRunner
    from llauncher.cli import app

    runner = CliRunner()
    with patch("llauncher.operations.list_orphans", return_value=[]):
        result = runner.invoke(app, ["orphan", "list"])

    assert result.exit_code == 0
    # The human-facing message is a contract too: silence would leave
    # the operator wondering whether the command ran.
    assert "no orphan" in result.stdout.lower()


def test_cli_orphan_list_empty_json_is_array() -> None:
    """``llauncher orphan list --json`` with zero orphans emits ``[]``.

    Pins ``cli.py:274-276``. The ``--json`` branch must always emit
    valid JSON; a refactor that emitted nothing on the empty case
    (because the table-print branch handles "empty" with a message)
    would break ``jq`` consumers.
    """
    from typer.testing import CliRunner
    from llauncher.cli import app

    runner = CliRunner()
    with patch("llauncher.operations.list_orphans", return_value=[]):
        result = runner.invoke(app, ["orphan", "list", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == []


# ---------------------------------------------------------------------------
# Audit idempotency on disk: same orphan does not duplicate audit entries
# ---------------------------------------------------------------------------


def test_refresh_orphans_emits_observed_orphan_once_per_lifetime(
    run_dir: Path, audit_path: Path
) -> None:
    """Pin ADR-015 §3: first-sighting writes one audit line; repeats are silent.

    Covers the dedupe interaction between
    ``state.LauncherState.refresh_orphans`` (``state.py`` 127-173) and
    ``operations.orphan.record_observed_orphan`` (``orphan.py`` 109-125).
    The existing unit test mocks ``record_observed_orphan`` out — this
    regression exercises the real audit append path against a tmp
    audit file so a refactor that moves the dedupe check below the
    record call (or removes it) is caught by a duplicated JSON line on
    disk.
    """
    from llauncher.state import LauncherState
    from llauncher.models.config import ChangeRules

    s = LauncherState.__new__(LauncherState)
    s.models = {}
    s.running = {}
    s.audit = []
    s.rules = ChangeRules()
    s.orphans = []
    s._observed_orphan_pids = set()
    s._warned_unreadable_pids = set()

    scan = [OrphanInfo(pid=13001, port=8085)]
    with patch("llauncher.state.list_orphans", return_value=scan):
        s.refresh_orphans()
        s.refresh_orphans()
        s.refresh_orphans()

    entries = al.read_entries(path=audit_path)
    orphan_entries = [
        e for e in entries if e.action == AuditAction.OBSERVED_ORPHAN
    ]
    assert len(orphan_entries) == 1, (
        f"expected one OBSERVED_ORPHAN audit line per pid-sighting; got "
        f"{[(e.action, e.pid) for e in orphan_entries]}"
    )
    e = orphan_entries[0]
    assert e.pid == 13001
    assert e.port == 8085
    assert e.result == AuditResult.SUCCESS


def test_refresh_orphans_disappear_reappear_emits_twice(
    run_dir: Path, audit_path: Path
) -> None:
    """A pid that leaves and re-enters the scan emits a fresh audit line.

    Pins the prune-on-disappearance contract in ``state.py:170`` against
    the real audit file. A refactor that made the dedupe set
    persistent across-prune (or that simply never pruned) would write
    only one line for the second sighting.
    """
    from llauncher.state import LauncherState
    from llauncher.models.config import ChangeRules

    s = LauncherState.__new__(LauncherState)
    s.models = {}
    s.running = {}
    s.audit = []
    s.rules = ChangeRules()
    s.orphans = []
    s._observed_orphan_pids = set()
    s._warned_unreadable_pids = set()

    seen = [OrphanInfo(pid=13002, port=8086)]
    empty: list[OrphanInfo] = []

    with patch(
        "llauncher.state.list_orphans", side_effect=[seen, empty, seen]
    ):
        s.refresh_orphans()  # first sighting → audit
        s.refresh_orphans()  # disappeared → prune
        s.refresh_orphans()  # reappeared → audit again

    entries = al.read_entries(path=audit_path)
    orphan_entries = [
        e for e in entries
        if e.action == AuditAction.OBSERVED_ORPHAN and e.pid == 13002
    ]
    assert len(orphan_entries) == 2, (
        "pid 13002 left the scan and reappeared; the audit log "
        "must reflect both sightings"
    )


def test_refresh_orphans_unreadable_pid_never_audits(
    run_dir: Path, audit_path: Path
) -> None:
    """``cmdline_unreadable=True`` pids do NOT write OBSERVED_ORPHAN entries.

    Pins ADR-015 §4: we cannot honestly classify managed-vs-unmanaged
    without argv, so the audit log must not record those pids. The
    operator-visible surface is the WARNING log (covered by
    test_orphan.py); the audit-log silence is pinned here against a
    real tmp audit file.
    """
    from llauncher.state import LauncherState
    from llauncher.models.config import ChangeRules

    s = LauncherState.__new__(LauncherState)
    s.models = {}
    s.running = {}
    s.audit = []
    s.rules = ChangeRules()
    s.orphans = []
    s._observed_orphan_pids = set()
    s._warned_unreadable_pids = set()

    scan = [OrphanInfo(pid=13003, port=None, cmdline_unreadable=True)]
    with patch("llauncher.state.list_orphans", return_value=scan):
        s.refresh_orphans()
        s.refresh_orphans()

    entries = al.read_entries(path=audit_path)
    orphan_entries = [
        e for e in entries if e.action == AuditAction.OBSERVED_ORPHAN
    ]
    assert orphan_entries == [], (
        "OBSERVED_ORPHAN must not be recorded for unreadable-cmdline pids; "
        "got {!r}".format([(e.pid, e.message) for e in orphan_entries])
    )
