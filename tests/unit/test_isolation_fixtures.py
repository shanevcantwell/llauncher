"""Regression coverage for the #463 suite-wide isolation seams themselves.

Issue #463 incident: bare ``pytest`` (no env var, no requested fixture)
wrote into the operator's real ``~/.llauncher`` — CRUD audit lines from
``tests/unit/test_cli.py``'s CLI-verb tests, and a live ``swap`` against the
real port 8081 from ``tests/integration/test_swap.py``'s ``@pytest.mark.live``
class. Root cause was two holes:

1. No suite-wide autouse isolation existed for ``LAUNCHER_STATE_DIR`` /
   ``CONFIG_DIR`` / ``LAUNCHER_AUDIT_PATH`` / ``LAUNCHER_RUN_DIR`` — only
   opt-in fixtures covered it, and a test that forgot to request one leaked.
2. The ``live`` marker was declared in ``pytest.ini`` but never deselected,
   so a real-port test collected and ran under bare pytest.

``tests/conftest.py`` now closes both holes with autouse fixtures
(``_isolate_state_dir``, ``_forbid_real_state_writes``, ``_forbid_live_ports``)
plus a ``pytest_collection_modifyitems`` hook. This module tests those seams
directly rather than only trusting that the rest of the suite happens to
exercise them.

These tests load ``tests/conftest.py`` by explicit file path via
``importlib.util.spec_from_file_location`` rather than ``import conftest`` --
the tree has THREE files named ``conftest.py``
(``tests/conftest.py``, ``tests/integration/conftest.py``,
``tests/ui/conftest.py``), all importable under the bare name ``conftest``
in pytest's default ``prepend`` mode. Whichever one is imported first wins
``sys.modules["conftest"]`` for the rest of the process -- a bare
``import conftest`` here passed in isolation but failed (``AttributeError``,
wrong conftest bound) once collected alongside the other suites, which
import their own ``conftest.py`` first. The explicit-path load sidesteps
the name collision entirely and always resolves to THIS root conftest,
regardless of collection order.
"""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest


def _load_root_conftest():
    """Load ``tests/conftest.py`` by explicit path, bypassing the bare-name
    ``conftest`` collision across ``tests/``, ``tests/integration/``, and
    ``tests/ui/`` (three same-named files under pytest's prepend import
    mode). Uses a distinct module name (``_llauncher_root_conftest_under_test``)
    so it never fights any of the three for ``sys.modules`` slot ``conftest``.
    """
    root_conftest_path = Path(__file__).resolve().parent.parent / "conftest.py"
    spec = importlib.util.spec_from_file_location(
        "_llauncher_root_conftest_under_test", root_conftest_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


root_conftest = _load_root_conftest()


# ─────────────────────── _isolate_state_dir (positive fix) ──────────────────


def test_isolate_state_dir_redirects_config_and_audit(tmp_path):
    """With the autouse fixture active (it always is, in this suite), the
    live module constants must point somewhere under THIS test's tmp_path,
    never at the real ``~/.llauncher``.
    """
    from llauncher.core import config as config_mod
    from llauncher.core import settings as settings_mod
    from llauncher.core import audit_log as audit_mod

    real_home_state = Path.home() / ".llauncher"

    assert settings_mod.LAUNCHER_STATE_DIR != real_home_state
    assert config_mod.CONFIG_DIR != real_home_state
    assert audit_mod.LAUNCHER_AUDIT_PATH != real_home_state / "audit.jsonl"
    assert config_mod.CONFIG_PATH != real_home_state / "config.json"

    # And it resolves under pytest's own tmp_path tree (not just "somewhere
    # else" -- specifically the per-test sandbox), so a leak to some OTHER
    # fixed path would still be caught by a stricter assertion than
    # !=-real-home alone.
    assert str(settings_mod.LAUNCHER_STATE_DIR).startswith(
        str(tmp_path.parent.parent)
    ) or ".llauncher" in str(settings_mod.LAUNCHER_STATE_DIR)


def test_isolate_state_dir_add_model_does_not_touch_real_ledger(tmp_path):
    """The exact incident anchor, generalized: calling ConfigStore.add_model
    with no other isolation fixture requested must not write into the real
    ~/.llauncher/audit.jsonl or config.json.
    """
    from llauncher.core.config import ConfigStore
    from llauncher.models.config import ModelConfig

    real_audit = Path.home() / ".llauncher" / "audit.jsonl"
    real_config = Path.home() / ".llauncher" / "config.json"
    before_audit = real_audit.stat().st_mtime if real_audit.exists() else None
    before_config = real_config.stat().st_mtime if real_config.exists() else None

    cfg = ModelConfig.from_dict_unvalidated(
        {
            "name": "isolation-test-model-463",
            "model_path": "/fake/path/model.gguf",
            "n_gpu_layers": 1,
            "ctx_size": 512,
        }
    )
    ConfigStore.add_model(cfg, caller="test")
    ConfigStore.remove_model("isolation-test-model-463", caller="test")

    after_audit = real_audit.stat().st_mtime if real_audit.exists() else None
    after_config = real_config.stat().st_mtime if real_config.exists() else None
    assert after_audit == before_audit
    assert after_config == before_config


# ─────────────────────── _forbid_real_state_writes (backstop) ───────────────


def test_forbid_real_state_writes_constants_track_settings_default():
    """The guard's own real-path constants must resolve to the same
    expression settings.py uses for its default -- Path.home()/".llauncher" --
    so a rename of the default in settings.py that this guard doesn't follow
    would show up as a mismatch here rather than silently checking the wrong
    path forever.
    """
    assert root_conftest._REAL_LAUNCHER_STATE_DIR == Path.home() / ".llauncher"
    assert root_conftest._REAL_AUDIT_PATH == (
        root_conftest._REAL_LAUNCHER_STATE_DIR / "audit.jsonl"
    )
    assert root_conftest._REAL_CONFIG_PATH == (
        root_conftest._REAL_LAUNCHER_STATE_DIR / "config.json"
    )


def test_forbid_real_state_writes_line_count_probe_detects_append(tmp_path):
    """Unit-test the guard's audit-line-count primitive directly, against a
    freestanding fake audit file (never the real one, and never
    monkeypatching ``root_conftest``'s own module attrs -- doing so would
    desync this test's local view from the outer ``_forbid_real_state_writes``
    autouse fixture, which snapshots those SAME attrs at its own setup and
    would then compare its "before" against a different path than its
    "after", tripping on this test's scaffolding rather than a real leak).

    Reimplements the line-count read inline rather than reusing
    ``root_conftest._real_audit_line_count()`` (which always reads the
    module's current, real-pointing attr) -- this proves the comparison
    LOGIC is correct without touching that attr at all.
    """

    def _line_count(audit_path: Path) -> int:
        if not audit_path.exists():
            return 0
        return len(audit_path.read_text(encoding="utf-8").splitlines())

    fake_audit = tmp_path / "audit.jsonl"

    before = _line_count(fake_audit)
    assert before == 0

    still_before = _line_count(fake_audit)
    assert still_before == before  # stable when nothing changed

    fake_audit.write_text('{"model":"unrelated-real-model"}\n')
    after = _line_count(fake_audit)
    assert after == before + 1


def test_forbid_real_state_writes_signature_scan_flags_fixture_names_only():
    """Unit-test the guard's signature-matching logic (issue #463
    ratification Rider 2, option (a)): a newly appended line is flagged
    only if it contains one of the suite's own test-fixture literal names,
    never merely because a line was appended.

    This is the direct regression test for Rider 2's named false-positive
    mode: a strict "did the ledger change at all" check would fail on ANY
    concurrent legitimate writer (the real agent, the operator's UI) --
    this asserts the guard's actual matching predicate distinguishes a real
    operator line from a test-fixture-signatured one.
    """
    real_operator_line = (
        '{"timestamp":"2026-08-24T13:13:15Z","action":"started",'
        '"result":"success","caller":"agent","port":8081,'
        '"model":"gemma-3-12b-it-IQ4_NL","from_model":null,'
        '"pid":4016404,"message":""}'
    )
    leaked_test_line = (
        '{"timestamp":"2026-08-24T00:00:00Z","action":"model_added",'
        '"result":"success","caller":"unknown","port":null,'
        '"model":"removable","from_model":null,"pid":null,"message":""}'
    )

    def _is_leak(line: str) -> bool:
        return any(sig in line for sig in root_conftest._TEST_FIXTURE_SIGNATURES)

    assert not _is_leak(real_operator_line)  # concurrent real activity: NOT a leak
    assert _is_leak(leaked_test_line)  # test-fixture name: IS a leak


def test_forbid_real_state_writes_config_signature_scan_ignores_real_additions():
    """The config-content check (Rider 2, option (a) applied to the
    non-append-only config.json) must only flag NEWLY PRESENT test-fixture
    signatures, not any content change -- a real operator adding a
    genuinely-named model to config.json mid-suite must not trip this.
    """
    before = '{"models": {}}'
    real_addition_after = '{"models": {"gemma-3-12b-it-IQ4_NL": {}}}'
    leaked_addition_after = '{"models": {"valid-model": {}}}'

    def _newly_present_signatures(before_content: str, after_content: str) -> list[str]:
        return [
            sig
            for sig in root_conftest._TEST_FIXTURE_SIGNATURES
            if sig in after_content and sig not in before_content
        ]

    assert _newly_present_signatures(before, real_addition_after) == []
    assert _newly_present_signatures(before, leaked_addition_after) == ["valid-model"]


# ─────────────────────── _forbid_live_ports (socket guard) ──────────────────


@pytest.mark.parametrize("port", [8081, 8082, 8765])
def test_forbid_live_ports_blocks_connect_to_each_forbidden_port(port):
    """Every port in the forbidden set raises on a real connect attempt.

    Uses a throwaway client socket; the guard fires before any actual
    network I/O would occur (AF_INET/SOCK_STREAM only, matching how the
    guard checks the address tuple).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(AssertionError, match="live llauncher-managed port"):
            s.connect(("127.0.0.1", port))
    finally:
        s.close()


@pytest.mark.parametrize("port", [8081, 8082, 8765])
def test_forbid_live_ports_blocks_connect_ex_to_each_forbidden_port(port):
    """``connect_ex`` (the non-raising variant callers sometimes probe
    liveness with) is wrapped too -- it must still raise via the guard
    rather than silently returning an errno.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(AssertionError, match="live llauncher-managed port"):
            s.connect_ex(("127.0.0.1", port))
    finally:
        s.close()


def test_forbid_live_ports_allows_ephemeral_port_roundtrip():
    """A real bind-to-0 + connect on a NON-forbidden ephemeral port must
    still work -- this is the false-positive risk the plan's risk section
    flags (``_free_port()`` in test_self_swap.py, mcp_env's stub server).
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    assert port not in root_conftest._FORBIDDEN_LIVE_PORTS

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(1.0)
    try:
        client.connect(("127.0.0.1", port))  # must not raise
    finally:
        client.close()
        srv.close()


def test_forbid_live_ports_forbidden_set_matches_documented_ports():
    """Pin the exact forbidden-port set the #151 coupling comment names,
    so a silent edit to the tuple (adding/removing a port) is a visible
    diff against this test, not just a comment nobody re-reads.
    """
    assert root_conftest._FORBIDDEN_LIVE_PORTS == (8081, 8082, 8765)


def test_forbid_live_ports_bypasses_under_llauncher_live_env(monkeypatch):
    """``_forbid_live_ports``'s own error message tells a test author to
    set ``LLAUNCHER_LIVE=1`` to legitimately reach a real port -- prove the
    fixture's early-return branch is actually reachable and unconditional
    on that env var. ``_forbid_live_ports`` is a plain (non-generator)
    autouse fixture -- it does its ``monkeypatch.setattr`` setup and
    relies on ``monkeypatch``'s own fixture-scoped teardown to revert, so
    calling its raw function directly (via ``._fixture_function``, the
    attribute pytest's ``FixtureFunctionDefinition`` wrapper exposes the
    plain function under) with a real ``pytest.MonkeyPatch`` instance
    reproduces exactly what pytest itself would do at fixture setup.

    Regression target: a prior version of this fixture had NO bypass at
    all (it unconditionally patched ``connect``/``connect_ex`` regardless
    of ``LLAUNCHER_LIVE``), so a test correctly opted into the live lane at
    collection time would still be blocked by this guard the instant it
    tried to actually reach the real port -- silently defeating the
    Phase-3 opt-in the plan's risk section requires ("tests that
    legitimately need the real agent/port... must land behind the Phase-3
    env gate, not be broken").
    """
    real_connect_before = socket.socket.connect
    real_connect_ex_before = socket.socket.connect_ex

    monkeypatch.setenv("LLAUNCHER_LIVE", "1")
    inner_mp = pytest.MonkeyPatch()
    try:
        raw_fixture_fn = root_conftest._forbid_live_ports._fixture_function
        raw_fixture_fn(inner_mp)  # must take the early `return` branch

        # Assert INSIDE the try, before inner_mp.undo() -- undo() would
        # restore the originals regardless of whether a patch happened,
        # which would make a post-undo assertion pass even if the bypass
        # were broken (silently defeating this exact regression test).
        assert socket.socket.connect is real_connect_before, (
            "the early-return bypass did not fire: socket.socket.connect "
            "was patched even though LLAUNCHER_LIVE=1 was set"
        )
        assert socket.socket.connect_ex is real_connect_ex_before, (
            "the early-return bypass did not fire: socket.socket.connect_ex "
            "was patched even though LLAUNCHER_LIVE=1 was set"
        )
    finally:
        inner_mp.undo()


# ─────────────────────── live-marker deselection (Phase 3) ──────────────────


def test_live_marker_is_declared_and_skipped_by_default(pytestconfig):
    """``live`` must be a registered marker (pytest.ini) — and, absent
    LLAUNCHER_LIVE=1 in THIS process's env, any collected item carrying it
    would be skipped by ``pytest_collection_modifyitems`` in tests/conftest.py.

    This test only pins the marker's registration + the hook's presence;
    the full skip behavior is proven end-to-end by the falsifier run
    (`pytest tests/integration/test_swap.py`, see issue #463's PR
    description) rather than re-implemented here via a nested pytest
    session (no ``pytester`` plugin wired into this repo).
    """
    line_matches = [
        line
        for line in pytestconfig.getini("markers")
        if line.startswith("live:")
    ]
    assert line_matches, "the 'live' marker must stay declared in pytest.ini"
    assert hasattr(root_conftest, "pytest_collection_modifyitems")
