"""Configuration for the llauncher agent service."""

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration for running an agent on a node."""

    host: str = Field(default="127.0.0.1", description="Host to bind the agent to")
    port: int = Field(default=8765, ge=1024, le=65535, description="Port to bind the agent to")
    node_name: str | None = Field(default=None, description="Friendly name for this node (defaults to hostname)")

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """Create config from environment variables.

        Supports:
        - LLAUNCHER_AGENT_HOST: Host to bind to (default: 127.0.0.1)
        - LLAUNCHER_AGENT_PORT: Port to bind to (default: 8765)
        - LLAUNCHER_AGENT_NODE_NAME: Friendly name for this node

        Per security hardening §3 C2, default bind is loopback. Operators
        opt into LAN exposure explicitly by setting ``LLAUNCHER_AGENT_HOST``
        (``0.0.0.0`` remains a valid value).
        """
        import os

        return cls(
            host=os.getenv("LLAUNCHER_AGENT_HOST", "127.0.0.1"),
            port=int(os.getenv("LLAUNCHER_AGENT_PORT", "8765")),
            node_name=os.getenv("LLAUNCHER_AGENT_NODE_NAME"),
        )
