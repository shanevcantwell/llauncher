"""Regression tests for issue #143 — agent stays responsive during a swap.

The agent verb handlers (``/swap``, ``/start``, ``/stop``) call synchronous
operations that block for the entire duration of a GPU model load (most of
that time is spent in ``proc.wait_for_server_ready``'s ``time.sleep`` poll
loop). Before #143 those handlers were ``async def``, so FastAPI ran them
directly on the single event loop — a swap stalled *every* concurrent
request, including ``GET /health``. Auto-restart/monitoring systems then
falsely declared the agent dead mid-load.

The fix declares the blocking handlers as plain ``def`` so Starlette
dispatches them to a worker threadpool, leaving the event loop free to
answer ``/health`` immediately.

Test strategy: drive the in-process ASGI app over ``httpx.ASGITransport``
on one event loop. Replace ``ops.swap`` with a fake that performs a **real**
``time.sleep`` (a faithful stand-in for a blocking GPU load). Fire the swap
as a background task, then call ``/health``.

The crisp regression signal is **ordering**, not wall-clock timing: on a
single event loop, if the swap blocks the loop, the test's own ``await``s
also stall, so the swap runs to completion *before* ``/health`` can even
start. We therefore assert that ``/health`` returns while the swap task is
still in flight (``not swap_task.done()``). With the ``async def``
regression that assertion fails; with the ``def`` fix it passes because the
blocking sleep runs off-loop in the threadpool.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time

import httpx
import pytest

from llauncher.agent import routing
from llauncher.agent.server import create_app_unauthenticated
from llauncher.operations.swap import SwapResult


# The fake swap blocks for this long. It must be comfortably larger than the
# time the event loop needs to dispatch the handler to the threadpool and
# answer /health, so the ordering assertion has a wide margin and does not
# flake on a busy CI host.
_SWAP_BLOCK_SECONDS = 2.0


@pytest.fixture
def app():
    """In-process agent app with auth disabled (test-only constructor)."""
    return create_app_unauthenticated()


def test_blocking_verb_handlers_are_sync_def() -> None:
    """Structural guard: the blocking handlers must NOT be coroutine functions.

    A coroutine handler would run its synchronous, blocking op directly on
    the event loop. Keeping them plain ``def`` is what lets Starlette offload
    them to a threadpool. ``health_check`` stays ``async def`` so it is always
    answered on the loop without waiting for a threadpool slot.
    """
    for fn in (routing.swap_server, routing.start_server, routing.stop_server):
        assert not inspect.iscoroutinefunction(fn), (
            f"{fn.__name__} must be a plain `def` so Starlette offloads its "
            "blocking op to a worker thread (issue #143). Reverting it to "
            "`async def` re-introduces the event-loop stall."
        )

    assert inspect.iscoroutinefunction(routing.health_check), (
        "health_check must stay `async def` so it is served on the event "
        "loop and never waits for a threadpool slot."
    )


async def test_health_responsive_during_blocking_swap(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /health`` returns while a blocking swap is still in progress."""
    swap_entered = threading.Event()

    def fake_swap(model_name, port, *, caller="unknown", **_kwargs):
        # Signal that we're executing, then block like a real GPU load would.
        swap_entered.set()
        time.sleep(_SWAP_BLOCK_SECONDS)
        return SwapResult(
            success=True,
            action="swapped",
            port_state="serving",
            port=port,
            model=model_name,
            previous_model="OldModel.f16",
            pid=4321,
            message=f"Swapped OldModel.f16 → {model_name} on port {port}",
        )

    # routing calls ``ops.swap(...)``; ``routing.ops`` is the operations
    # package, so patching its ``swap`` attribute redirects the handler.
    monkeypatch.setattr(routing.ops, "swap", fake_swap)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://agent.test"
    ) as client:
        swap_task = asyncio.create_task(
            client.post("/swap/8082", json={"model": "LFM2-350M-Pro.f16"})
        )

        # Yield to the loop so the swap request reaches the handler and the
        # blocking op is handed to the threadpool. If the handler were
        # ``async def``, this sleep would itself stall for the full block
        # because the loop is blocked — and the swap would already be done
        # below.
        await asyncio.sleep(0.05)

        health_resp = await client.get("/health")

        # The load-bearing assertion: health came back while the swap is
        # still running. A blocked event loop would have finished the swap
        # before health could start.
        assert not swap_task.done(), (
            "swap completed before /health returned — the event loop was "
            "blocked by the synchronous swap (issue #143 regression)."
        )
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "healthy"

        # The swap was genuinely executing, not skipped.
        assert swap_entered.is_set()

        # Drain the background swap so the task doesn't outlive the client.
        swap_resp = await swap_task
        assert swap_resp.status_code == 200
        assert swap_resp.json()["action"] == "swapped"
