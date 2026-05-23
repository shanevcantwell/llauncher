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

    with pytest.raises(ValueError, match="non-empty auth_token"):
        create_app(auth_token=None)  # type: ignore[arg-type]


def test_create_app_rejects_empty_string() -> None:
    """C1 (#87): create_app(auth_token="") raises ValueError.

    Empty-string would otherwise be a quietly-mis-typed env var that
    skips auth construction — the exact failure mode the C1 invariant
    is meant to foreclose.
    """
    from llauncher.agent.server import create_app

    with pytest.raises(ValueError, match="non-empty auth_token"):
        create_app(auth_token="")


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
