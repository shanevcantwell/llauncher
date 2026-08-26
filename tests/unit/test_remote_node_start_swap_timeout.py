"""Issue #503: ``RemoteNode.start_server``/``.swap_server`` must not time out
on a slow-but-successful agent response.

The agent's ``/start`` and ``/swap`` handlers are deliberately synchronous
(``agent/routing.py``) and can legitimately take up to the readiness ceiling
(``DEFAULT_READINESS_TIMEOUT_S`` = 120 s) to answer — a slow model load, or
the 5-phase swap mechanic. Pre-fix, ``RemoteNode`` used the node's flat
``self.timeout`` (default 5.0 s, sized for quick status/control calls) for
every verb including these two, so a client-side ``httpx`` timeout fired
while the agent was still working and the call was misreported as failed
even though the agent went on to complete it.

These tests drive a *real* HTTP server (``tests._fake_slow_agent``) that
sleeps past the old 5 s default before answering 200, over a real
``RemoteNode`` built with the node's ordinary short ``timeout=5.0`` default
— proving the fix does not depend on the caller widening the node's own
timeout, only on the verb-specific client override in
``RemoteNode._get_client``.
"""

from __future__ import annotations

import time

from llauncher.remote.node import RemoteNode, NodeStatus
from tests._fake_slow_agent import slow_fake_agent


def _node(port: int) -> RemoteNode:
    # Explicit short timeout matching the node's own default (5.0s) — the
    # point of the test is that start_server/swap_server survive a longer
    # agent delay *despite* this short node-level timeout.
    return RemoteNode("test-node", "127.0.0.1", port=port, timeout=5.0)


class TestStartServerSurvivesSlowAgent:
    def test_start_server_reports_success_past_5s(self):
        with slow_fake_agent(
            delay_s=6.0,
            body={"success": True, "action": "started", "message": "Started m on port 8080"},
        ) as port:
            node = _node(port)
            t0 = time.monotonic()
            result = node.start_server("m", 8080)
            elapsed = time.monotonic() - t0

        assert elapsed >= 6.0, "test agent must actually have blocked past 5s"
        assert result is not None
        assert result["success"] is True
        assert node.status == NodeStatus.ONLINE

    def test_other_verbs_keep_the_short_default(self):
        """A verb outside the #503 scope (get_status) must still honor the
        node's short ``self.timeout`` — the fix is per-verb, not global."""
        with slow_fake_agent(delay_s=6.0) as port:
            node = _node(port)
            result = node.get_status()

        # The 5s client timeout fires well before the 6s server delay
        # answers, so this must fail (offline), not hang for 6s+.
        assert result is None
        assert node.status == NodeStatus.OFFLINE


class TestSwapServerSurvivesSlowAgent:
    def test_swap_server_reports_success_past_5s(self):
        with slow_fake_agent(
            delay_s=6.0,
            body={"success": True, "action": "swapped", "message": "Swapped to m on port 8080"},
        ) as port:
            node = _node(port)
            t0 = time.monotonic()
            result = node.swap_server("m", 8080)
            elapsed = time.monotonic() - t0

        assert elapsed >= 6.0, "test agent must actually have blocked past 5s"
        assert result is not None
        assert result["success"] is True
        assert node.status == NodeStatus.ONLINE
