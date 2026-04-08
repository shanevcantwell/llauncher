"""FastAPI server for the llauncher agent service.

This agent wraps the local LauncherState and exposes it over HTTP,
allowing a head dashboard to manage this node remotely.

Usage:
    llauncher-agent
    # or with custom config
    LAUNCHER_AGENT_PORT=9000 LAUNCHER_AGENT_NODE_NAME="my-node" llauncher-agent
"""

import logging
import socket
import sys

import uvicorn
from fastapi import FastAPI

from llauncher.agent.config import AgentConfig
from llauncher.agent.routing import router, get_node_name

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="llauncher Agent",
        description="Remote management agent for llauncher nodes",
        version="0.1.0",
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
    # Load config from environment
    config = AgentConfig.from_env()

    try:
        run_agent(config)
    except KeyboardInterrupt:
        logger.info("Agent shutdown requested")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Agent failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
