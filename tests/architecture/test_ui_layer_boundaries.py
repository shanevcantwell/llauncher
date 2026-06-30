"""Static architecture-invariant guard for the ``llauncher.ui`` endpoint layer.

Why this test exists
--------------------
``ui/`` is an **endpoint** layer (``.claude/architecture.md``). The "one rule"
is *dependencies point downward; siblings do not import siblings*. Concretely,
a UI tab reaches the backend **only** through the orchestration facades
(``state`` / ``operations``) and remote nodes **only** through ``remote/``
(``NodeRegistry`` / ``RemoteNode`` / ``RemoteAggregator``) — the single
sanctioned HTTP client. A UI module must therefore never:

* import a direct-HTTP library (``httpx`` / ``requests`` / ``urllib`` /
  ``http.client`` / ``socket`` / ``aiohttp``) and hit a node URL itself —
  node I/O is ``remote/``'s job; or
* import ``llauncher.agent.*`` or ``llauncher.mcp_server.*`` (peer endpoints
  across the network boundary) or ``llauncher.cli`` (a sibling endpoint).

This is not hypothetical. A past UI tab shipped a cross-layer reach — a tab
talking to a node directly over HTTP instead of through the engine — and it
did not surface until **after** an alpha tag. This guard is the deterministic,
fail-fast catch for that bug class: it scans the AST of every ``ui/`` module at
test time, so the violation fails CI on the commit that introduces it, not in
production.

If this test FAILS, a UI module has reached across a layer boundary. The fix is
never to loosen this guard — it is to route the call through the engine: backend
verbs via ``state``/``operations``, node I/O via ``remote/``. See
``.claude/architecture.md`` (the layer map and the forbidden-edge table) and
ADR-025.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Repo root = two parents up from this file: tests/architecture/<file>.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_UI_ROOT = _REPO_ROOT / "llauncher" / "ui"
_ARCH_DOC = ".claude/architecture.md"

# Direct-HTTP libraries a UI module must never import. Node I/O is the job of
# ``remote/`` (the sanctioned client); a UI module constructing/hitting a URL
# itself is exactly the cross-layer reach this guard exists to catch.
_HTTP_ROOTS = frozenset(
    {"httpx", "requests", "urllib3", "urllib", "socket", "aiohttp", "pycurl", "http3"}
)
# ``http`` is stdlib and mostly harmless (e.g. ``http.HTTPStatus``); only
# ``http.client`` is a direct-HTTP transport, so it is matched specifically
# rather than blanket-banning the ``http`` namespace.
_HTTP_DOTTED = ("http.client",)

# Peer/sibling endpoints across the layer map. ``ui`` may depend *downward*
# (state / operations / remote / core / models / util) and *within itself*,
# but never sideways into another endpoint.
_FORBIDDEN_LL_PREFIXES = (
    "llauncher.agent",
    "llauncher.mcp_server",
    "llauncher.cli",
)


def _iter_ui_files() -> list[Path]:
    return sorted(p for p in _UI_ROOT.rglob("*.py"))


def _module_for(path: Path) -> str:
    """Dotted module path of a file inside the repo (``llauncher.ui.tabs.x``)."""
    rel = path.relative_to(_REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(path: Path, level: int, module: str | None) -> str:
    """Resolve a relative import (``from . import x``) to an absolute module."""
    pkg_parts = _module_for(path).split(".")
    # A module's own package is its parent; ``level`` rises from there.
    base = pkg_parts[: len(pkg_parts) - level]
    if module:
        base = base + module.split(".")
    return ".".join(base)


def _is_http(module: str) -> bool:
    root = module.split(".", 1)[0]
    if root in _HTTP_ROOTS:
        return True
    return any(module == d or module.startswith(d + ".") for d in _HTTP_DOTTED)


def _is_forbidden_sibling(module: str) -> bool:
    return any(
        module == p or module.startswith(p + ".") for p in _FORBIDDEN_LL_PREFIXES
    )


def _classify(module: str) -> str | None:
    """Return a violation category for ``module``, or ``None`` if allowed."""
    if _is_http(module):
        return "direct-HTTP transport"
    if _is_forbidden_sibling(module):
        return "sibling/peer endpoint import"
    return None


def _imported_modules(tree: ast.AST, path: Path) -> list[tuple[str, int]]:
    """Yield ``(absolute_module, lineno)`` for every import in ``tree``."""
    found: list[tuple[str, int]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for alias in n.names:
                found.append((alias.name, n.lineno))
        elif isinstance(n, ast.ImportFrom):
            if n.level and n.level > 0:
                base = _resolve_relative(path, n.level, n.module)
            else:
                base = n.module or ""
            # Record the ``from X import ...`` base module.
            if base:
                found.append((base, n.lineno))
            # Also record ``from X import a, b`` as ``X.a`` / ``X.b`` so that
            # ``from http import client`` or ``from urllib import request`` is
            # caught even though the base (``http`` / ``urllib``) alone might
            # not be flagged on its own.
            for alias in n.names:
                if alias.name == "*":
                    continue
                dotted = f"{base}.{alias.name}" if base else alias.name
                found.append((dotted, n.lineno))
    return found


def _scan() -> list[str]:
    """Return a human-readable violation line for every boundary breach."""
    violations: list[str] = []
    for path in _iter_ui_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = path.relative_to(_REPO_ROOT)
        for module, lineno in _imported_modules(tree, path):
            category = _classify(module)
            if category is None:
                continue
            violations.append(
                f"  {rel}:{lineno}: imports '{module}' ({category}). "
                "ui/ must reach the backend only DOWNWARD — backend verbs via "
                "state/operations, node I/O via remote/ (NodeRegistry / "
                "RemoteNode / RemoteAggregator), never its own HTTP nor a peer "
                "endpoint."
            )
    return violations


def test_ui_modules_make_no_forbidden_imports():
    """No ``ui/`` module imports a direct-HTTP lib or a peer/sibling endpoint.

    This is the deterministic catch for the "UI tab reaches across a layer /
    hits a node URL directly" bug class (the alpha regression). A failure here
    is a *real* architecture violation in production code — fix the offending
    module to work through the engine/remote facade, do not relax this guard.
    """
    assert _UI_ROOT.is_dir(), f"ui/ layer not found at {_UI_ROOT}"
    # Sanity: the scan must actually see the UI modules, otherwise an empty
    # tree would make this vacuously green.
    assert _iter_ui_files(), f"no ui/ modules discovered under {_UI_ROOT}"

    violations = _scan()
    assert not violations, (
        "llauncher/ui/ violated its layer boundary (see "
        f"{_ARCH_DOC} — the layer map and forbidden-edge table; ADR-025):\n"
        + "\n".join(violations)
        + "\n\nui/ is an ENDPOINT layer: dependencies point DOWNWARD and "
        "siblings do not import siblings. Route backend calls through "
        "state/operations and all node I/O through remote/."
    )


def test_guard_actually_detects_a_planted_violation():
    """Meta-test: the scanner must flag known-bad imports, not silently pass.

    Guards that can only ever pass are worthless. We feed the classifier the
    exact import shapes of the alpha bug and assert each is caught, so a future
    refactor that neuters the scanner (e.g. drops ``http.client`` matching)
    fails here instead of going quietly blind.
    """
    must_catch = [
        "httpx",
        "requests",
        "urllib",
        "urllib.request",
        "http.client",
        "socket",
        "aiohttp",
        "llauncher.agent.auth",
        "llauncher.mcp_server.server",
        "llauncher.cli",
    ]
    for module in must_catch:
        assert _classify(module) is not None, f"scanner blind to {module!r}"

    # ...and must NOT flag legitimate downward / stdlib / intra-ui imports
    # (guard against over-strictness / false positives on valid code).
    must_allow = [
        "streamlit",
        "llauncher.state",
        "llauncher.operations",
        "llauncher.remote.registry",
        "llauncher.remote.node",
        "llauncher.core.settings",
        "llauncher.models.config",
        "llauncher.ui.components.node_selector",
        "pandas",
        "os",
        "http",  # bare stdlib http (e.g. http.HTTPStatus) is fine
        "http.server",  # not a client transport
    ]
    for module in must_allow:
        assert _classify(module) is None, f"scanner false-positive on {module!r}"
