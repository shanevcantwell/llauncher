"""Regression tests for issue #87 (security plan §3 C1 invariant).

``create_app`` must refuse to construct a FastAPI app without a
non-empty ``auth_token``. The only sanctioned path to a no-auth app is
``create_app_unauthenticated`` (test-only).

These tests guard against a future caller silently passing ``None`` /
``""`` and getting an unauthenticated production app — the failure mode
that originally motivated the C1 hardening.
"""

from __future__ import annotations

import pytest


def test_create_app_rejects_none_token() -> None:
    """C1 (#87): create_app() with no args raises TypeError (required arg)."""
    from llauncher.agent.server import create_app

    with pytest.raises(TypeError):
        create_app()  # type: ignore[call-arg]


def test_create_app_rejects_explicit_none() -> None:
    """C1 (#87): create_app(auth_token=None) raises ValueError, not silent no-auth."""
    from llauncher.agent.server import create_app

    with pytest.raises(ValueError, match="non-empty"):
        create_app(auth_token=None)  # type: ignore[arg-type]


def test_create_app_rejects_empty_string() -> None:
    """C1 (#87): create_app(auth_token="") raises ValueError.

    Empty-string would otherwise be a quietly-mis-typed env var that
    skips auth construction — the exact failure mode the C1 invariant
    is meant to foreclose.
    """
    from llauncher.agent.server import create_app

    with pytest.raises(ValueError, match="non-empty"):
        create_app(auth_token="")


@pytest.mark.parametrize("ws_token", [" ", "  ", "\t", "\n", " \t \n "])
def test_create_app_rejects_whitespace_only_token(ws_token: str) -> None:
    """C1 (#111): whitespace-only tokens must be rejected.

    A typo like ``LAUNCHER_AGENT_TOKEN=" "`` (or a trailing newline from
    a misread token file) is non-falsy but semantically empty. Without
    a ``strip()`` check, ``create_app`` would build an app whose auth
    middleware compares incoming ``X-Api-Key`` against the whitespace
    string — effectively no protection. The whitespace cases here pin
    that the check is content-based, not just truthiness-based.
    """
    from llauncher.agent.server import create_app

    with pytest.raises(ValueError, match="non-whitespace"):
        create_app(auth_token=ws_token)


def test_create_app_with_token_wires_auth_middleware() -> None:
    """Happy path: a non-empty token attaches the auth middleware."""
    from llauncher.agent.server import create_app
    from llauncher.agent.middleware import AuthenticationMiddleware

    app = create_app(auth_token="test-token-abc")
    middleware_classes = {m.cls for m in app.user_middleware}
    assert AuthenticationMiddleware in middleware_classes


def test_create_app_unauthenticated_omits_auth_middleware() -> None:
    """The sibling helper builds an app with no auth middleware."""
    from llauncher.agent.server import create_app_unauthenticated
    from llauncher.agent.middleware import AuthenticationMiddleware

    app = create_app_unauthenticated()
    middleware_classes = {m.cls for m in app.user_middleware}
    assert AuthenticationMiddleware not in middleware_classes


def test_create_app_unauthenticated_tripwire_documented() -> None:
    """The unauthenticated builder carries a SECURITY docstring tagging its scope.

    A future maintainer reading the symbol should immediately see that
    it is a test-only escape hatch from the C1 invariant.
    """
    from llauncher.agent.server import create_app_unauthenticated

    doc = create_app_unauthenticated.__doc__ or ""
    assert "SECURITY" in doc
    assert "test-only" in doc.lower()


def test_create_app_unauthenticated_uses_runtime_guard_not_assert() -> None:
    """C1 (#112): the production-mode guard must survive ``python -O``.

    ``assert __debug__`` would be stripped by the optimizer at the
    exact configuration it is meant to defend against, leaving the
    no-auth builder silently reachable in optimized production
    builds. The guard must be a real runtime check
    (``if not __debug__: raise RuntimeError(...)``) — pin that shape
    in source via ``ast`` so a future refactor cannot quietly regress
    to an ``assert`` form. We parse the function rather than substring-
    match because the docstring legitimately mentions ``assert __debug__``
    when explaining why we don't use it.
    """
    import ast
    import inspect

    from llauncher.agent.server import create_app_unauthenticated

    src = inspect.getsource(create_app_unauthenticated)
    tree = ast.parse(src)
    # The function is the sole top-level node of the parsed source.
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)

    # No `assert __debug__` statement may appear in the function body.
    for node in ast.walk(func):
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Name):
            assert node.test.id != "__debug__", (
                "create_app_unauthenticated has `assert __debug__` in its "
                "body — python -O strips asserts, so this becomes a no-op "
                "in optimized production builds (issue #112). Use "
                "`if not __debug__: raise RuntimeError(...)` instead."
            )

    # And the runtime-guard shape must be present.
    found_runtime_guard = False
    for node in ast.walk(func):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.UnaryOp)
            and isinstance(node.test.op, ast.Not)
            and isinstance(node.test.operand, ast.Name)
            and node.test.operand.id == "__debug__"
        ):
            # Body must include a `raise RuntimeError(...)`.
            for child in ast.walk(node):
                if isinstance(child, ast.Raise) and isinstance(child.exc, ast.Call):
                    exc_name = (
                        child.exc.func.id
                        if isinstance(child.exc.func, ast.Name)
                        else ""
                    )
                    if exc_name == "RuntimeError":
                        found_runtime_guard = True
                        break
    assert found_runtime_guard, (
        "create_app_unauthenticated must contain "
        "`if not __debug__: raise RuntimeError(...)` (issue #112)."
    )
