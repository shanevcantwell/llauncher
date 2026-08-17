"""Defect C — bogus start_time / uptime_seconds ≈ 0.

ENCODES CURRENT BUGGY BEHAVIOR (RED-pin). Passes while the bug is present;
invert/remove when fixed. See README.md and issue #181.

Symptom: a server's reported uptime is always ~0 because
``refresh_running_servers`` hardcodes ``start_time=datetime.now()`` and
ignores the lockfile's real ``started_at``.

Code path:
  - ``state.py::refresh_running_servers`` (line 119) constructs
    ``RunningServer(..., start_time=datetime.now())`` with the inline comment
    "We don't track actual start time".
  - ``models/config.py::RunningServer.uptime_seconds`` then computes
    ``datetime.now() - start_time`` → ≈ 0.

Expected-correct: ``start_time`` derives from the lockfile's ``started_at``
(here set to ~2 hours ago), giving a realistic uptime. Actual-buggy: the
reported ``start_time`` is ~now and uptime ≈ 0, regardless of the lockfile.

This script: writes a lockfile with ``started_at`` ~2h in the past, scans a
live fake, and asserts the reported ``start_time`` is ~now (and uptime tiny),
i.e. the lockfile's ``started_at`` was ignored.
"""

from __future__ import annotations

import _repro_lib as L

tmp = L.make_temp_env()

from datetime import datetime, timedelta, timezone  # noqa: E402

from llauncher.core import lockfile as lf  # noqa: E402


def run() -> bool:
    fake = None
    try:
        started_2h_ago = datetime.now(timezone.utc) - timedelta(hours=2)

        fake = L.FakeServer(L.FAKE_PORT, L.FAKE_MODEL_PATH)

        lock = lf.Lockfile(
            pid=fake.pid,
            model="fake-card",
            port=L.FAKE_PORT,
            started_at=started_2h_ago.isoformat(),
            llauncher_pid=999999,
        )
        lf.lockfile_path(L.FAKE_PORT).write_text(
            __import__("json").dumps(lock.to_dict())
        )

        st = L.fresh_state_with_models(
            {"fake-card": L.make_model_config("fake-card", L.FAKE_MODEL_PATH)}
        )
        before = datetime.now()
        st.refresh_running_servers()
        after = datetime.now()

        assert L.FAKE_PORT in st.running, (
            f"precondition: live fake should be scannable; running={st.running}"
        )
        rs = st.running[L.FAKE_PORT]

        # BUG: start_time is ~now (between before/after), NOT the lockfile's
        # started_at (~2h ago).
        assert before <= rs.start_time <= after, (
            "EXPECTED buggy: start_time should be ~now (datetime.now()), got "
            f"{rs.start_time.isoformat()} outside [{before}, {after}]"
        )
        uptime = rs.uptime_seconds()
        assert uptime < 5, (
            f"EXPECTED buggy ~0 uptime, got {uptime}s"
        )
        # And it ignored the real ~7200s lockfile age.
        real_age = (datetime.now(timezone.utc) - started_2h_ago).total_seconds()
        assert real_age > 7000, "sanity: lockfile age should be ~2h"
        print(
            f"  lockfile started_at was ~{int(real_age)}s ago, but reported "
            f"uptime_seconds()={uptime} (start_time hardcoded to now())"
        )
        return True
    finally:
        if fake is not None:
            fake.terminate_and_reap()
        try:
            lf.remove_lockfile(L.FAKE_PORT)
        except Exception:
            pass
        L.assert_no_fake_leaks()


if __name__ == "__main__":
    import sys

    sys.exit(0 if run() else 1)
