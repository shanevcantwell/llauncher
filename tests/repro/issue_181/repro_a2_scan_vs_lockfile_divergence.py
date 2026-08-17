"""Defect A2 — UI scan path and operations lockfile path DISAGREE.

ENCODES CURRENT BUGGY BEHAVIOR (RED-pin). Passes while the bug is present;
invert/remove when fixed. See README.md and issue #181.

Symptom: two unsynchronized models of "what is running" can give opposite
answers about whether a port is stoppable. The UI eject gates on an argv
scan cached once per session (``state.can_stop`` /
``refresh_running_servers``); the MCP/operations stop reads the
authoritative lockfile fresh on every call (``operations/stop.py::stop`` →
``_reconcile_for_stop``). When a process starts AFTER the UI's cached scan
ran but BEFORE its next rescan, the two paths diverge: operations sees the
live lockfile claim immediately, the UI is still looking at its pre-launch
snapshot.

Code path:
  - UI: ``state.py::refresh_running_servers`` (line 91, argv scan, run once
    at ``LauncherState`` construction and cached in ``st.session_state`` —
    ``ui/app.py:23``) → ``state.py::can_stop`` (line 243) →
    ``(False, "No server running…")`` when the port isn't in the stale
    snapshot.
  - Operations: ``operations/stop.py::_reconcile_for_stop`` (line 62) →
    ``lf.read_lockfile(port)`` returns the live claim, uncached → must
    terminate.

Construction for a deterministic, GPU-free divergence — via a REAL code
path, not a hand-set dict:

  - ``ui/app.py:23`` caches ``LauncherState`` once in ``st.session_state``
    and only re-scans when something explicitly calls
    ``refresh_running_servers()`` again. Between reruns, ``self.running`` is
    whatever the LAST scan saw — a real staleness window, not a fiction.
  - We reproduce that window with real ordering, not with a forced value:
    1. Build ``LauncherState`` (its constructor runs a REAL
       ``refresh_running_servers()`` scan) while NOTHING is running yet —
       so ``self.running`` is genuinely empty, because the scan genuinely
       found nothing.
    2. *Afterwards*, spawn a live fake and write its lockfile — mirroring a
       server started by another caller (CLI/MCP) after the UI's session
       state was cached and before its next rerun.
    3. We deliberately do **not** call ``st.refresh_running_servers()``
       again — this is the same "no rerun happened yet" gap
       ``st.session_state`` produces in the live UI.
  - The operations path reads the lockfile fresh on every call (it has no
    equivalent cache), so it sees the newly-live claim immediately.

Assertion: ``can_stop`` says NOT stoppable (from the real, now-stale scan)
while ``_reconcile_for_stop`` hands back a live lockfile (i.e. "must
terminate") for the SAME port — an explicit, reproduced divergence arising
from real staleness, not from assigning ``st.running`` by hand. We DO NOT
call ``operations.stop.stop`` itself (that would SIGKILL the fake — fine —
but we assert on the reconcile decision to keep the divergence the thing
under test).
"""

from __future__ import annotations

import _repro_lib as L

tmp = L.make_temp_env()

from datetime import datetime, timezone  # noqa: E402

import importlib  # noqa: E402

from llauncher.core import lockfile as lf  # noqa: E402

# NOTE: ``llauncher.operations.__init__`` re-exports the ``stop`` *function*,
# which shadows the ``stop`` *submodule* attribute on the package. So we load
# the submodule by its fully-qualified name to reach ``_reconcile_for_stop``.
stop_op = importlib.import_module("llauncher.operations.stop")


def run() -> bool:
    fake = None
    try:
        # --- UI path, step 1: build LauncherState BEFORE anything is
        # running. Its constructor runs a REAL refresh_running_servers()
        # scan; self.running comes out genuinely empty because the scan
        # genuinely found nothing (there is nothing to find yet).
        st = L.fresh_state_with_models(
            {"Qwen3.5-27B-UD-Q6_K_XL": L.make_model_config(
                "Qwen3.5-27B-UD-Q6_K_XL", L.FAKE_MODEL_PATH)}
        )
        assert L.FAKE_PORT not in st.running, (
            "precondition: nothing should be running yet, but the real scan "
            f"already reports port {L.FAKE_PORT}: {st.running}"
        )

        # --- Now bring up a LIVE fake the operations path can legitimately
        # stop — mirroring a server started by another caller (CLI/MCP)
        # after the UI's session-cached state was built.
        fake = L.FakeServer(L.FAKE_PORT, L.FAKE_MODEL_PATH)

        # Authoritative lockfile in the TEMP run dir claims the port for the
        # live pid. Written by hand (not write_lockfile) so we control the
        # recorded started_at and avoid O_EXCL collisions.
        lock = lf.Lockfile(
            pid=fake.pid,
            model="Qwen3.5-27B-UD-Q6_K_XL",  # the minted/launched identity
            port=L.FAKE_PORT,
            started_at=datetime(2026, 6, 20, 20, 21, 22, tzinfo=timezone.utc).isoformat(),
            llauncher_pid=999999,
        )
        lock_path = lf.lockfile_path(L.FAKE_PORT)
        lock_path.write_text(__import__("json").dumps(lock.to_dict()))

        # --- Operations path (lockfile-authoritative) ---
        # No cache: reads the lockfile fresh, so it sees the newly-live
        # claim immediately.
        early, existing = stop_op._reconcile_for_stop(L.FAKE_PORT, caller="mcp")
        # Live claim -> no early result, hands back the lockfile to terminate.
        assert early is None and existing is not None, (
            "operations path should see a LIVE lockfile claim needing "
            f"termination; got early={early}, existing={existing}"
        )
        ops_stoppable = early is None and existing is not None

        # --- UI path, step 2: deliberately do NOT re-scan. This is the
        # real ``st.session_state`` gap (ui/app.py:23) — the UI's cached
        # state object is not refreshed on every rerun, so ``self.running``
        # is still the pre-launch snapshot from step 1.
        ui_ok, ui_msg = st.can_stop(L.FAKE_PORT, caller="ui")
        assert ui_ok is False, "EXPECTED buggy: UI can_stop refuses"

        # --- The divergence, asserted explicitly ---
        assert ops_stoppable != ui_ok, (
            "EXPECTED DIVERGENCE: operations path says stoppable="
            f"{ops_stoppable} while UI path says stoppable={ui_ok}"
        )
        print(
            f"  DIVERGENCE on port {L.FAKE_PORT}: operations(lockfile) -> "
            f"STOPPABLE (live claim for {existing.model!r}); "
            f'UI(scan) -> (False, "{ui_msg}")'
        )
        return True
    finally:
        if fake is not None:
            fake.terminate_and_reap()
        # Remove our temp lockfile (it lives in the temp dir anyway).
        try:
            lf.remove_lockfile(L.FAKE_PORT)
        except Exception:
            pass
        L.assert_no_fake_leaks()


if __name__ == "__main__":
    import sys

    sys.exit(0 if run() else 1)
