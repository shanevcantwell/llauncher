"""Local node-info payload builder (issues #62, #125).

The node-info payload is pure local-state introspection — node name,
hostname, OS, OS version, Python version, and resolved IP addresses. Two
peers across the network boundary need the *identical* payload:

* ``agent.routing.node_info`` serves it over ``GET /node-info``;
* ``remote.RemoteNode.get_node_info`` returns it in-process on the #62
  self-loop short-circuit (issue #125), skipping the loopback HTTP hop.

Per the layer doctrine (``docs/ARCHITECTURE.md``), ``remote`` must not
import ``agent`` — they are peers across the wire. The shared builder is
therefore hoisted *down* into ``core`` rather than reached for sideways,
so both the server endpoint and the client self-loop path can call it.
"""

from __future__ import annotations

import os
import platform
import socket


def get_node_name() -> str:
    """Resolve this node's friendly name.

    Sourced from ``LLAUNCHER_AGENT_NODE_NAME`` (the agent's bind-time
    identity, see ``agent.config``), falling back to the OS hostname.

    Uses a falsy-or fallback rather than ``os.getenv``'s default argument:
    the default argument only fires when the variable is *absent*, but a
    present-and-empty value (e.g. an installer-written ``VAR=`` env block
    entry) must fall back too (#367).
    """
    return os.getenv("LLAUNCHER_AGENT_NODE_NAME") or socket.gethostname()


def get_node_info() -> dict:
    """Build the local node-info payload.

    Pure introspection of this process's host: never raises on a
    misconfigured resolver — IP discovery degrades to an empty list
    rather than propagating.
    """
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        addr_info = socket.getaddrinfo(hostname, None)
        ips = list(set(str(addr[4][0]) for addr in addr_info))
    except Exception:
        pass

    return {
        "node_name": get_node_name(),
        "hostname": socket.gethostname(),
        "os": platform.system(),
        "os_version": platform.version(),
        "python_version": platform.python_version(),
        "ip_addresses": ips,
    }
