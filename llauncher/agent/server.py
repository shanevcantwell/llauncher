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

import uvicorn
from fastapi import FastAPI

from llauncher import __version__
from llauncher.agent.config import AgentConfig
from llauncher.agent.routing import router, get_node_name

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


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="llauncher Agent",
        description="Remote management agent for llauncher nodes",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
    )

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
    logger.info(f"API docs: http://{config.host}:{config.port}/docs")

    # Warning if binding to all interfaces without auth
    if config.host == "0.0.0.0":
        logger.warning(
            "Agent is binding to 0.0.0.0 (all interfaces). "
            "Ensure this is a trusted network. "
            "Use LAUNCHER_AGENT_HOST to bind to a specific interface."
        )

    # Run the server
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
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
        # Normal exit if run_agent completed successfully
        sys.exit(0)
    except KeyboardInterrupt:
        logger.info("Agent requested shutdown")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Agent failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
