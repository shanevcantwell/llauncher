"""Defect B — first-match misattribution under model_path collision.

ENCODES CURRENT BUGGY BEHAVIOR (RED-pin). Passes while the bug is present;
invert/remove when fixed. See README.md and issue #181.

Symptom: two ModelConfigs share ONE model_path (live incident:
``Qwen3.5-27B-UD-Q6_K_XL`` and ``…-nommproj`` → same ``.gguf``). The running
server's identity is authoritative in the lockfile/``--alias`` (the mint,
EMIT-CANONICAL), but ``refresh_running_servers`` reverse-maps argv→config by
model_path and returns the FIRST dict match — which can be the wrong sibling.
``server_status``/``list_models`` then report the wrong card.

Code path:
  - ``state.py::refresh_running_servers`` (line 113) calls
    ``_find_model_by_path(model_path)``.
  - ``state.py::_find_model_by_path`` (line 175) iterates ``self.models``
    and ``return``s the FIRST ``name`` whose ``config.model_path`` matches —
    insertion-order-dependent, ignoring the launched ``--alias``.

Expected-correct: identity comes from the lockfile's ``model`` (the
launched/authoritative name). Actual-buggy: the reverse-match returns the
first-inserted sibling, which differs from the launched identity.

This script: one shared gguf; the SECOND config is the launched/authoritative
identity (recorded in a lockfile), but the FIRST config is dict-first.
Asserts the reported ``config_name`` == first sibling != authoritative.
"""

from __future__ import annotations

import _repro_lib as L

tmp = L.make_temp_env()

from datetime import datetime, timezone  # noqa: E402

from llauncher.core import lockfile as lf  # noqa: E402


def run() -> bool:
    fake = None
    try:
        # Insertion order matters: the FIRST sibling is what _find_model_by_path
        # returns. We deliberately make the AUTHORITATIVE (launched) identity
        # the SECOND one, so the bug surfaces as wrong attribution.
        first_sibling = "Qwen3.5-27B-UD-Q6_K_XL-nommproj"   # dict-first
        authoritative = "Qwen3.5-27B-UD-Q6_K_XL"            # launched/--alias
        models = {
            first_sibling: L.make_model_config(first_sibling, L.SHARED_MODEL_PATH),
            authoritative: L.make_model_config(authoritative, L.SHARED_MODEL_PATH),
        }

        fake = L.FakeServer(L.FAKE_PORT, L.SHARED_MODEL_PATH)

        # The lockfile records the AUTHORITATIVE launched identity (the mint).
        lock = lf.Lockfile(
            pid=fake.pid,
            model=authoritative,
            port=L.FAKE_PORT,
            started_at=datetime.now(timezone.utc).isoformat(),
            llauncher_pid=999999,
        )
        lf.lockfile_path(L.FAKE_PORT).write_text(
            __import__("json").dumps(lock.to_dict())
        )

        st = L.fresh_state_with_models(models)
        st.refresh_running_servers()

        assert L.FAKE_PORT in st.running, (
            "precondition: the LIVE fake should be scannable on the port; "
            f"running={st.running}"
        )
        reported = st.running[L.FAKE_PORT].config_name

        # BUG: reported identity is the dict-first sibling, NOT the launched
        # (lockfile/--alias) identity.
        assert reported == first_sibling, (
            f"EXPECTED buggy first-match: reported {reported!r}, "
            f"expected dict-first {first_sibling!r}"
        )
        assert reported != lock.model, (
            "EXPECTED misattribution: reported config_name should DIFFER from "
            f"the authoritative lockfile identity {lock.model!r}, but matched"
        )
        print(
            f"  scan attributes port {L.FAKE_PORT} to {reported!r} "
            f"(dict-first) but the lockfile/--alias mint says {lock.model!r}"
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
