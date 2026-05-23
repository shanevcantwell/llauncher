"""FastAPI server for the llauncher agent service.

This agent wraps the local LauncherState and exposes it over HTTP,
allowing a head dashboard to manage this node remotely.

Usage:
    llauncher-agent
    # or with custom config
    LAUNCHER_AGENT_PORT=9000 LAUNCHER_AGENT_NODE_NAME="my-node" llauncher-agent
    # stop running agent
    llauncher-agent --stop
"""

import argparse
import logging
import os
import signal
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from llauncher import __version__
from llauncher import operations as ops
from llauncher.agent.auth import is_loopback, resolve_agent_token
from llauncher.agent.config import AgentConfig
from llauncher.agent.middleware import (
    AuthenticationMiddleware,
    BodySizeLimitMiddleware,
)
from llauncher.agent.routing import router, get_node_name
from llauncher.core import lockfile as lf
from llauncher.core.settings import AGENT_API_KEY

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def find_process_on_port(port: int) -> int | None:
    """Find the PID of the process listening on the given port.

    Args:
        port: Port number to check.

    Returns:
        PID of the process, or None if not found.
    """

    # Try to find process using /proc on Linux
    if sys.platform == "linux":
        import glob

        for fd_path in glob.glob(f"/proc/*/fd/*"):
            try:
                fd = int(fd_path.split("/")[-1])
                link = os.readlink(fd_path)
                if "socket:" in link:
                    # Get the process ID
                    pid = int(fd_path.split("/")[2])
                    # Check if this socket is bound to our port
                    # by reading /proc/net/tcp
                    with open("/proc/net/tcp") as f:
                        for line in f:
                            if ":%.4X " % port in line:
                                return pid
            except (ValueError, OSError, FileNotFoundError):
                continue
        return None

    # On Windows, we'd need to use netstat or wmi
    # For now, just return None and let the caller handle it
    return None


def stop_agent(port: int) -> bool:
    """Stop any agent running on the given port.

    Args:
        port: Port the agent is listening on.

    Returns:
        True if agent was stopped, False if no agent found or error.
    """
    try:
        # Try to connect to the agent's health endpoint
        import httpx

        response = httpx.get(f"http://localhost:{port}/health", timeout=2.0)
        if response.status_code == 200:
            # Agent is running, try to find and kill it
            pid = find_process_on_port(port)
            if pid:
                os.kill(pid, signal.SIGTERM)
                logger.info(f"Agent (PID {pid}) terminated")
                return True

            # Fallback: try to find via socket
            import psutil

            for conn in psutil.net_connections(kind="tcp"):
                if conn.laddr.port == port and conn.status == "LISTEN":
                    try:
                        proc = conn.pid
                        p = psutil.Process(proc)
                        p.terminate()
                        logger.info(f"Agent (PID {proc}) terminated")
                        return True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

            logger.warning("Agent is running but could not find process to terminate")
            return False
        else:
            logger.info("No agent responding on port")
            return False
    except httpx.RequestError:
        logger.info("No agent running on port")
        return False
    except Exception as e:
        logger.error(f"Error stopping agent: {e}")
        return False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan: reap llauncher-managed llama-server children on shutdown.

    Per Issue #65 (Phase 2 of the v2 phased plan): uvicorn 0.35 captures both
    SIGTERM and SIGINT and drains in-flight HTTP requests, but the agent does
    not reap the llama-server children it spawned because those processes are
    started with ``start_new_session=True`` (own process group) and therefore
    do not receive the agent's signals transitively.

    On shutdown we enumerate the per-port lockfile registry — the durable
    record of llauncher-managed children — and dispatch each through the
    existing :func:`operations.stop` verb, which handles audit emission,
    psutil-based termination, and lockfile removal.

    Symmetric on SIGTERM and SIGINT: any agent shutdown reaps children. This
    is a behavior change from the previous bare ``KeyboardInterrupt`` path,
    which orphaned children silently. See ``docs/v2-handoff.md`` §What NOT
    To Do for context.
    """
    # Startup — nothing to do.
    yield

    # Shutdown — reap managed children. We catch OSError specifically because
    # the lockfile directory may be missing or unreadable; we do NOT use a
    # bare ``except Exception`` here per the ADR-tightened convention from
    # #61. Per-port failures are logged and skipped so one bad lockfile
    # cannot abort the reap loop.
    try:
        lockfiles = lf.list_lockfiles()
    except OSError as exc:
        logger.error("Lifespan shutdown: cannot enumerate lockfiles: %s", exc)
        return

    if not lockfiles:
        logger.info("Lifespan shutdown: no managed llama-server children to reap")
        return

    logger.info(
        "Lifespan shutdown: reaping %d managed llama-server child(ren)",
        len(lockfiles),
    )
    for entry in lockfiles:
        try:
            result = ops.stop(entry.port, caller="agent-shutdown")
        except OSError as exc:
            logger.error(
                "Lifespan shutdown: stop(port=%d) failed: %s", entry.port, exc
            )
            continue
        logger.info(
            "Lifespan shutdown: port=%d action=%s model=%s",
            entry.port,
            result.action,
            result.model,
        )


# SECURITY (§3 C1 / issue #87): ``create_app`` requires a non-empty
# ``auth_token`` so no caller can silently construct an unauthenticated
# app. The production entry point (``run_agent`` below) resolves a token
# via env / stdin / file / auto-generate before reaching this function;
# test-only construction without auth must go through
# ``create_app_unauthenticated`` so the no-auth path is grep-able and
# never reachable from production code paths.
def create_app(auth_token: str) -> FastAPI:
    """Create and configure the FastAPI application with auth enforced.

    **No CORS by design** (security plan §3 control C4): this app
    intentionally registers no CORS middleware and emits no
    ``Access-Control-*`` response headers. Browsers therefore cannot
    make cross-origin requests to the agent from arbitrary pages — a
    drive-by from any website is blocked by the same-origin policy
    before it ever reaches the auth layer. Do not add
    ``CORSMiddleware`` without first revisiting the threat model;
    ``tests/integration/test_agent_cors.py`` pins this posture as a
    regression guard (plan §4 assertion C4-a).

    Args:
        auth_token: Non-empty token to enforce on incoming requests via
            the ``X-Api-Key`` header. Passing ``None`` or an empty
            string raises ``ValueError`` — callers that genuinely want
            an unauthenticated app must use
            :func:`create_app_unauthenticated`.

    Raises:
        ValueError: If ``auth_token`` is ``None`` or an empty string.
    """
    if not auth_token:
        raise ValueError(
            "create_app requires a non-empty auth_token. "
            "Use create_app_unauthenticated() for test-only no-auth construction."
        )
    return _build_app(auth_token=auth_token)


def create_app_unauthenticated() -> FastAPI:
    """Construct a FastAPI app with NO authentication middleware.

    SECURITY: This is a **test-only** constructor. Production code paths
    (``run_agent``) must use :func:`create_app` with a resolved token.
    The C1 invariant — "no non-loopback bind without an auth token" — is
    enforced at the ``run_agent`` callsite, not here, so this helper
    must never be reached from a production entry point.

    The ``__debug__`` tripwire below makes it observable when this
    helper is invoked in an optimized build (``python -O``): production
    deployments running with ``-O`` will trip the assertion and refuse
    to construct, while normal pytest runs (``__debug__`` is True) keep
    working unchanged.
    """
    # Tripwire: ``python -O`` strips asserts, so this fails fast in any
    # build flagged as a release/production interpreter. In dev/test
    # (``__debug__`` is True) the assertion is a no-op.
    assert __debug__, (
        "create_app_unauthenticated() must not be reached in optimized "
        "(production) builds — use create_app(auth_token=...) instead."
    )
    return _build_app(auth_token=None)


def _build_app(auth_token: str | None) -> FastAPI:
    """Internal shared builder. See ``create_app`` / ``create_app_unauthenticated``."""
    auth_active = auth_token is not None

    # Disable OpenAPI docs endpoints when authentication is configured
    docs_url = None if auth_active else "/docs"
    redoc_url = None if auth_active else "/redoc"

    app = FastAPI(
        title="llauncher Agent",
        description="Remote management agent for llauncher nodes",
        version=__version__,
        openapi_url=None if auth_active else "/openapi.json",
        docs_url=docs_url,
        redoc_url=redoc_url,
        lifespan=lifespan,
    )

    if auth_active:
        # Add authentication middleware when API key is configured
        app.add_middleware(AuthenticationMiddleware, expected_token=auth_token)

    # Body-size cap (security plan §3 C3 / issue #78). Registered last so
    # it becomes the outermost layer: oversize requests are rejected with
    # 413 before auth (or anything else) even sees the body, bounding the
    # worst-case memory pressure from a malicious or buggy client.
    app.add_middleware(BodySizeLimitMiddleware)

    # Include the router
    app.include_router(router, tags=["llauncher"])

    return app


def run_agent(config: AgentConfig) -> None:
    """Run the agent server.

    Enforces the security hardening §3 C1 guard: refuses to start
    when binding to a non-loopback interface without an authentication
    token configured. Auto-generates a token file at
    ``~/.llauncher/agent.token`` on the first loopback start with no
    env-provided token.

    Args:
        config: Agent configuration.

    Raises:
        SystemExit: With code 2 when binding non-loopback without an
            available authentication token. The error message names
            both remediation paths (set ``LAUNCHER_AGENT_TOKEN`` or
            bind loopback).
    """
    env_token = os.environ.get("LAUNCHER_AGENT_TOKEN")
    loopback = is_loopback(config.host)

    if not loopback:
        # Non-loopback bind: require a token from env, stdin, or the
        # token file. Do NOT auto-generate in this branch — auto-gen
        # is only safe for loopback (a freshly-generated secret that
        # nobody outside the host has seen is meaningless for LAN
        # exposure).
        token = resolve_agent_token(env_value=env_token, allow_generate=False)
        if token is None:
            sys.stderr.write(
                "[llauncher-agent] ERROR: refusing to bind to non-loopback host "
                f"{config.host!r} without an authentication token.\n"
                "[llauncher-agent] Set LAUNCHER_AGENT_TOKEN (or use "
                "LAUNCHER_AGENT_TOKEN=- to pipe a token on stdin), or bind "
                "to 127.0.0.1 to allow auto-generation of a local token.\n"
            )
            raise SystemExit(2)
    else:
        # Loopback bind: env > stdin > file > generate-and-write.
        token = resolve_agent_token(env_value=env_token, allow_generate=True)

    app = create_app(auth_token=token)

    # Log startup info
    node_name = config.node_name or socket.gethostname()
    logger.info(f"Starting llauncher agent on {config.host}:{config.port}")
    logger.info(f"Node name: {node_name}")

    # Token is always present at this point: the non-loopback branch
    # would have exited above, and the loopback branch generates a
    # token on first run if none was supplied.
    logger.info("Authentication is active. Binding to %s", config.host)

    # Run the server. ``lifespan="on"`` ensures the FastAPI lifespan handler
    # (which reaps llama-server children on shutdown per #65) actually fires
    # regardless of uvicorn's auto-detection heuristics.
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
        lifespan="on",
    )


def main() -> None:
    """Main entry point for the agent CLI."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="llauncher agent")
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop any running agent and exit",
    )
    args = parser.parse_args()

    # Handle --stop flag
    if args.stop:
        config = AgentConfig.from_env()
        success = stop_agent(config.port)
        if success:
            logger.info("Agent stopped successfully")
            sys.exit(0)
        else:
            logger.info("No running agent found to stop")
            sys.exit(0)
        # If we get here, sys.exit didn't exit (e.g., because it was mocked in tests)
        return

    # Load config from environment and start agent
    config = AgentConfig.from_env()

    try:
        run_agent(config)
        # Normal exit — uvicorn's capture_signals() handler returns cleanly
        # on SIGTERM after draining in-flight requests, and the FastAPI
        # lifespan shutdown handler (per #65) has already reaped managed
        # llama-server children at this point.
        logger.info("Agent shutdown complete")
        sys.exit(0)
    except KeyboardInterrupt:
        # Some uvicorn versions re-raise KeyboardInterrupt on SIGINT after
        # the lifespan shutdown handler runs. Treat identically to a clean
        # SIGTERM exit — children have already been reaped by the lifespan
        # handler.
        logger.info("Agent shutdown complete (interrupted)")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Agent failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
