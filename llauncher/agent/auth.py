"""Agent authentication helpers (security hardening §3 C1).

Loopback-host classification lives here. The token-resolution policy was
hoisted to :mod:`llauncher.core.agent_token` (issue #171) so that
``remote.*`` and the front-ends can read the agent token without importing
``agent.*`` — removing the ``remote → agent`` layering violation. The
public token-resolution names are re-exported below so existing
``from llauncher.agent.auth import resolve_agent_token`` callers keep
working unchanged.

Note for test authors: monkeypatching ``default_token_path`` /
``resolve_agent_token`` must target the canonical home
(``llauncher.core.agent_token``), because the implementations resolve their
collaborators (e.g. ``default_token_path``) through *that* module's
namespace. Patching the re-exported name here has no effect on the
implementation's internal lookups.
"""

from __future__ import annotations

# Re-export the hoisted token-resolution surface (issue #171). ``sys`` is
# re-exported too: a test monkeypatching ``agent.auth.sys.stdin`` patches
# the shared ``sys`` module object, which the core implementation also
# reads — so that seam keeps working across the move.
import sys  # noqa: F401  (re-exported for legacy ``auth_mod.sys`` patch sites)

from llauncher.core.agent_token import (  # noqa: F401
    _generate_and_persist_token,
    _read_stdin_token,
    _read_token_file,
    default_token_path,
    resolve_agent_token,
)


LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def is_loopback(host: str) -> bool:
    """Return True if ``host`` is a recognized loopback address.

    The set of loopback hosts is the closed set ``{127.0.0.1, ::1,
    localhost}``. Anything else — including ``0.0.0.0`` (all
    interfaces) and any LAN/WAN address — is treated as non-loopback.
    """
    return host in LOOPBACK_HOSTS
