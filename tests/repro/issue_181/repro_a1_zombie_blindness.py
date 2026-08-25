"""Defect A1 — zombie blindness makes the UI eject a silent no-op.

ENCODES CURRENT BUGGY BEHAVIOR (RED-pin). Passes while the bug is present;
invert/remove when fixed. See README.md and issue #181.

Symptom (operator): clicking the live indicator's eject does nothing — the
model stays loaded — because the UI's running-state is an argv scan of the
process table, and a llama-server that has become a <defunct>/zombie is
INVISIBLE to that scan.

Code path:
  - ``core/process.py::find_server_by_port`` (~line 444) and
    ``find_all_llama_servers`` (~line 467) call ``proc.cmdline()`` inside a
    ``try`` that catches ``psutil.ZombieProcess`` and ``continue``s past it.
    A zombie's ``cmdline()`` raises ``ZombieProcess``; its ``name()`` is the
    real exe ("sleep"/"python"), not "llama-server" — so neither match arm
    fires. The pid is skipped entirely.
  - ``state.py::refresh_running_servers`` (line 91) iterates
    ``find_all_llama_servers()`` → the zombie never enters ``self.running``.
  - ``state.py::can_stop`` (line 243): ``port not in self.running`` →
    returns ``(False, "No server running on port {port}")`` — the verbatim
    eject-failure symptom — even though the pid still exists.

Expected-correct: a still-present pid claiming the port should be stoppable
(lockfile-authoritative truth, ADR-LLNCH-008). Actual-buggy: the kill is never
attempted; eject is a success-shaped no-op.

This script: forks a fake llama-server bound conceptually to port 18181,
SIGKILLs it WITHOUT reaping (zombie), then shows the scan can't see it and
``can_stop`` reports "No server running". Reaps in teardown.
"""

from __future__ import annotations

import _repro_lib as L

# Hermetic env BEFORE importing llauncher.
L.make_temp_env()

from llauncher.core import process as proc  # noqa: E402


def run() -> bool:
    zombie = None
    try:
        zombie = L.ZombieFake(L.FAKE_PORT, L.FAKE_MODEL_PATH)

        assert zombie.is_zombie(), (
            f"precondition: pid {zombie.pid} should be a zombie but is not"
        )
        # pid still exists (it's a zombie, not gone) — the eject *could* fire.
        import psutil

        assert psutil.pid_exists(zombie.pid), "zombie pid should still exist"

        # BUG 1: the port scanner cannot find the zombie.
        found = proc.find_server_by_port(L.FAKE_PORT)
        assert found is None, (
            f"EXPECTED buggy: find_server_by_port({L.FAKE_PORT}) returns None "
            f"for a zombie, but returned {found}"
        )

        # BUG 2: the zombie is absent from find_all_llama_servers().
        all_pids = {p.pid for p in proc.find_all_llama_servers()}
        assert zombie.pid not in all_pids, (
            "EXPECTED buggy: zombie pid should be absent from "
            f"find_all_llama_servers(), but found {zombie.pid} in {all_pids}"
        )

        # BUG 3: LauncherState.refresh() → running does NOT contain the port.
        st = L.fresh_state_with_models(
            {"fake-card": L.make_model_config("fake-card", L.FAKE_MODEL_PATH)}
        )
        st.refresh_running_servers()
        assert L.FAKE_PORT not in st.running, (
            f"EXPECTED buggy: port {L.FAKE_PORT} should be absent from "
            f"state.running, but present: {st.running}"
        )

        # BUG 4 (the verbatim eject symptom): can_stop reports no server.
        ok, msg = st.can_stop(L.FAKE_PORT, caller="ui")
        assert ok is False, "EXPECTED buggy: can_stop should refuse"
        assert msg == f"No server running on port {L.FAKE_PORT}", (
            f"EXPECTED the verbatim symptom message, got: {msg!r}"
        )

        print(
            f"  zombie pid {zombie.pid} EXISTS but is invisible to the scan; "
            f'can_stop(ui) -> (False, "{msg}")'
        )
        return True
    finally:
        if zombie is not None:
            zombie.reap()
        L.assert_no_fake_leaks()


if __name__ == "__main__":
    import sys

    sys.exit(0 if run() else 1)
