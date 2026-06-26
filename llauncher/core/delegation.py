"""Launch-delegation gate (issue #200).

System-mode deployment (#194) runs the agent as a dedicated service
account that is the *sole* spawner of ``llama-server`` processes. Operator
front-ends (the MCP server, the local-node UI path) must therefore stop
spawning in-process and instead **delegate** local-node launches to the
agent over HTTP — while standalone/dev workflows with no agent running
must keep working by falling back to an in-process spawn.

This module is the single decision point. It lives in
:mod:`llauncher.core` — *below* ``remote`` and ``agent`` in the layering —
so ``remote.node`` and the front-ends (``mcp_server``, ``ui``) can all
import it without any new ``remote → agent`` edge (cf. #171). It does NOT
import ``remote`` or ``agent`` itself: it answers only the *decision*; the
caller owns building the ``RemoteNode`` and POSTing when the answer is
"delegate".

Decision (for a LOCAL-node launch request):

* If the current process **is** the agent (:func:`is_agent_process`) →
  never delegate. The agent talking to itself uses in-process ops; this
  preserves the #62 self-call optimization.
* Otherwise (a front-end): honor an explicit
  ``LLAUNCHER_DELEGATE_TO_LOCAL_AGENT`` override when set to a recognized
  truthy/falsy value (deterministic service deployments).
* Otherwise auto-detect: if a healthy local agent answers
  ``GET /health`` on ``LLAUNCHER_AGENT_PORT`` (short timeout, with the
  resolved ``X-Api-Key``) → delegate; else fall back to in-process.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


# Env var the agent process stamps on itself at startup (before
# ``uvicorn.run``); see ``llauncher.agent.server.run_agent``.
AGENT_PROCESS_ENV = "LLAUNCHER_IS_AGENT_PROCESS"

# Operator override for the delegation decision. Truthy → always delegate;
# falsy → always in-process. Unset (or an unrecognized value) → auto-detect.
DELEGATE_OVERRIDE_ENV = "LLAUNCHER_DELEGATE_TO_LOCAL_AGENT"

# Short health-probe timeout. The probe sits on the launch hot path, so it
# must fail fast when no agent is listening rather than stalling the caller.
_HEALTH_PROBE_TIMEOUT_S = 1.0

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def _env_flag(value: str | None) -> bool | None:
    """Parse an env var into a tri-state: True / False / None (unrecognized).

    ``None`` covers both "unset" and "set to something we don't recognize",
    so callers fall through to auto-detection in either case.
    """
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    return None


def is_agent_process() -> bool:
    """Return True iff this process is the llauncher agent.

    Detection is an explicit env stamp (``LLAUNCHER_IS_AGENT_PROCESS``)
    that the agent sets on itself at startup, *before* ``uvicorn.run``.
    Any recognized-truthy value counts; unset/empty/falsy means "not the
    agent" — which is the safe default for front-ends.
    """
    return _env_flag(os.environ.get(AGENT_PROCESS_ENV)) is True


def local_agent_healthy(
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    token: str | None = None,
    timeout: float = _HEALTH_PROBE_TIMEOUT_S,
) -> bool:
    """Return True iff a local agent answers ``GET /health`` with 200.

    The probe carries the resolved agent token as ``X-Api-Key`` even
    though ``/health`` is auth-exempt — harmless if exempt, correct if a
    future build tightens the exemption. Any transport error or non-200
    is treated as "no healthy agent" (→ in-process fallback).

    ``port`` defaults to ``settings.AGENT_PORT`` (read at call time so test
    patches and reloaded settings take effect). ``token`` defaults to the
    resolved agent token; resolution failures degrade to an unauthenticated
    probe rather than raising.
    """
    if port is None:
        from llauncher.core import settings

        port = settings.AGENT_PORT
    if token is None:
        try:
            from llauncher.core.agent_token import resolve_agent_token

            token = resolve_agent_token(allow_generate=False)
        except Exception:  # noqa: BLE001 — probe must never raise
            token = None

    headers = {"X-Api-Key": token} if token else {}
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(
                f"http://{host}:{port}/health", headers=headers
            )
        return response.status_code == 200
    except httpx.RequestError:
        return False


def should_delegate(host: str = "127.0.0.1", port: int | None = None) -> bool:
    """Decide whether a front-end should delegate a local-node launch.

    ``host`` / ``port`` identify the *agent* endpoint to probe on the
    auto-detect path; they default to loopback and ``settings.AGENT_PORT``.

    Returns True → caller should POST the verb to the local agent over
    HTTP; False → caller should run the in-process ``operations`` verb.
    """
    # The agent itself never delegates — it IS the spawner (#62).
    if is_agent_process():
        return False

    override = _env_flag(os.environ.get(DELEGATE_OVERRIDE_ENV))
    if override is not None:
        return override

    return local_agent_healthy(host=host, port=port)


def local_agent_node():
    """Construct a ``RemoteNode`` aimed at the local agent (#200 delegation).

    Single construction point for the delegation target — host, port, and
    token live here rather than being copy-pasted into each front-end.
    The node is named ``"local"`` and carries the resolved ``X-Api-Key``;
    because a front-end is not the agent process, its ``_is_self_loop()``
    is False, so verb calls go over HTTP to ``127.0.0.1:AGENT_PORT``.

    ``RemoteNode`` is imported lazily: ``remote.node`` imports *this*
    module at load time, so a top-level import here would be circular. The
    lazy import keeps ``core`` free of a load-time edge to ``remote`` while
    still centralizing the construction.
    """
    from llauncher.core import settings
    from llauncher.core.agent_token import resolve_agent_token
    from llauncher.remote.node import RemoteNode

    token = resolve_agent_token(allow_generate=False)
    return RemoteNode("local", "127.0.0.1", port=settings.AGENT_PORT, api_key=token)
