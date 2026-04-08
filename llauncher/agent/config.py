"""Configuration for the llauncher agent service."""

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration for running an agent on a node."""

    host: str = Field(default="0.0.0.0", description="Host to bind the agent to")
    port: int = Field(default=8765, ge=1024, le=65535, description="Port to bind the agent to")
    node_name: str | None = Field(default=None, description="Friendly name for this node (defaults to hostname)")

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """Create config from environment variables.

        Supports:
        - LAUNCHER_AGENT_HOST: Host to bind to (default: 0.0.0.0)
        - LAUNCHER_AGENT_PORT: Port to bind to (default: 8765)
        - LAUNCHER_AGENT_NODE_NAME: Friendly name for this node
        """
        import os

        return cls(
            host=os.getenv("LAUNCHER_AGENT_HOST", "0.0.0.0"),
            port=int(os.getenv("LAUNCHER_AGENT_PORT", "8765")),
            node_name=os.getenv("LAUNCHER_AGENT_NODE_NAME"),
        )
