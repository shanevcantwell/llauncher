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
from llauncher.agent.config import AgentConfig
from llauncher.agent.middleware import AuthenticationMiddleware
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


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    auth_active = AGENT_API_KEY is not None

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
        app.add_middleware(AuthenticationMiddleware, expected_token=AGENT_API_KEY)

    # Include the router
    app.include_router(router, tags=["llauncher"])

    return app


def run_agent(config: AgentConfig) -> None:
    """Run the agent server.

    Args:
        config: Agent configuration.
    """
    app = create_app()

    # Log startup info
    node_name = config.node_name or socket.gethostname()
    logger.info(f"Starting llauncher agent on {config.host}:{config.port}")
    logger.info(f"Node name: {node_name}")

    if AGENT_API_KEY:
        logger.info("Authentication is active. Binding to %s", config.host)
    else:
        logger.warning("API key (LAUNCHER_AGENT_API_KEY) not set — no authentication enabled.")
        logger.info("API docs: http://%s:%s/docs", config.host, config.port)

    # Warning if binding to all interfaces without auth
    if AGENT_API_KEY is None:
        if config.host == "0.0.0.0":
            logger.warning(
                "Agent is binding to 0.0.0.0 (all interfaces). "
                "Ensure this is a trusted network. "
                "Use LAUNCHER_AGENT_HOST to bind to a specific interface."
            )
        elif config.host.startswith("192.168.") or config.host.startswith("10."):
            logger.info(
                "Agent binding to local address %s without authentication — "
                "ensure this network segment is trusted.",
                config.host,
            )

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
