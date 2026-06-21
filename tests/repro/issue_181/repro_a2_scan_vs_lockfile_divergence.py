"""Defect A2 — UI scan path and operations lockfile path DISAGREE.

ENCODES CURRENT BUGGY BEHAVIOR (RED-pin). Passes while the bug is present;
invert/remove when fixed. See README.md and issue #181.

Symptom: two unsynchronized models of "what is running" can give opposite
answers about whether a port is stoppable. The UI eject gates on an argv
scan (``state.can_stop`` / ``refresh_running_servers``); the MCP/operations
stop reads the authoritative lockfile (``operations/stop.py::stop`` →
``_reconcile_for_stop``). When the process is unscannable (here: a zombie)
but a valid lockfile still claims the port, the two paths diverge.

Code path:
  - UI: ``state.py::refresh_running_servers`` (line 91, argv scan) →
    ``state.py::can_stop`` (line 243) → ``(False, "No server running…")``.
  - Operations: ``operations/stop.py::_reconcile_for_stop`` (line 62) →
    ``lf.read_lockfile(port)`` returns the live claim → ``reconcile_lockfile``
    sees ``pid_alive`` (a zombie pid still "exists" but is NOT alive by
    ``is_pid_alive``; so to show the divergence cleanly we use a LIVE fake
    here whose lockfile is valid AND which the scan happens to miss because
    the UI state was populated from a stale/empty scan).

Construction for a deterministic, GPU-free divergence:
  - A valid lockfile is written into the TEMP ``LAUNCHER_RUN_DIR`` for
    port 18181 pointing at a LIVE fake pid (so the operations path sees a
    stoppable live claim).
  - The UI ``LauncherState`` is built from an EMPTY scan snapshot (mirroring
    the cached-``self.running`` staleness in ``st.session_state``,
    ``ui/app.py:23``): ``self.running`` does not contain the port, so
    ``can_stop`` refuses.

Assertion: ``can_stop`` says NOT stoppable while ``_reconcile_for_stop``
hands back a live lockfile (i.e. "must terminate") for the SAME port — an
explicit, reproduced divergence. We DO NOT call ``operations.stop.stop``
itself (that would SIGKILL the fake — fine — but we assert on the reconcile
decision to keep the divergence the thing under test).
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
        # A LIVE fake the operations path can legitimately stop.
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
        early, existing = stop_op._reconcile_for_stop(L.FAKE_PORT, caller="mcp")
        # Live claim -> no early result, hands back the lockfile to terminate.
        assert early is None and existing is not None, (
            "operations path should see a LIVE lockfile claim needing "
            f"termination; got early={early}, existing={existing}"
        )
        ops_stoppable = early is None and existing is not None

        # --- UI path (argv-scan-authoritative) ---
        # Build state whose scan snapshot does NOT include the port (the
        # cached-self.running staleness condition from the incident). We
        # construct from an empty scan and assert can_stop refuses.
        st = L.fresh_state_with_models(
            {"Qwen3.5-27B-UD-Q6_K_XL": L.make_model_config(
                "Qwen3.5-27B-UD-Q6_K_XL", L.FAKE_MODEL_PATH)}
        )
        # Simulate the stale cached snapshot: clear running (as if the scan
        # ran when the process was momentarily unscannable / before launch).
        st.running = {}
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
