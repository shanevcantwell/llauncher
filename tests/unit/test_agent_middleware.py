"""Tests for AuthenticationMiddleware in the llauncher agent service."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from llauncher.agent.middleware import AuthenticationMiddleware


def _make_app(token=None) -> tuple[FastAPI, TestClient]:
    """Create a test app optionally wrapped with AuthenticationMiddleware."""
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/docs")
    def docs_endpoint():  # proxy for OpenAPI docs path
        return {"openapi": True}

    @app.get("/openapi.json")
    def openapi_json():
        return {"schema": {}}

    @app.get("/redoc")
    def redoc_page():
        return {}

    @app.get("/protected")
    def protected():
        return {"data": "secret"}

    if token is not None:
        app.add_middleware(AuthenticationMiddleware, expected_token=token)

    return app, TestClient(app)


def test_no_token_allows_all_requests():
    """When no auth token is configured, all requests pass through."""
    app, client = _make_app(token=None)
    assert client.get("/health").status_code == 200
    assert client.get("/protected").status_code == 200


def test_with_token_rejects_unauthenticated_returns_401():
    """Missing X-Api-Key header should return 401."""
    app, client = _make_app(token="secret")
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_with_token_accepts_valid_key():
    """Correct X-Api-Key header should allow the request."""
    app, client = _make_app(token="secret")
    response = client.get("/protected", headers={"X-Api-Key": "secret"})
    assert response.status_code == 200


def test_with_token_rejects_wrong_key_returns_403():
    """Wrong X-Api-Key value should return 403."""
    app, client = _make_app(token="secret")
    response = client.get("/protected", headers={"X-Api-Key": "wrong"})
    assert response.status_code == 403


def test_openapi_docs_excluded_from_auth():
    """Exempt paths (/health, /docs, etc.) should be accessible without auth."""
    app, client = _make_app(token="secret")

    # Even with auth active, these endpoints are free
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").status_code in (200,)
    assert client.get("/redoc").status_code in (200,)


def test_with_empty_api_key_returns_403():
    """Empty string X-Api-Key is present but wrong — should return 403 (not 401)."""
    app, client = _make_app(token="secret")
    
    # Empty key means header was sent but value is empty → credentials provided, access denied
    response = client.get("/protected", headers={"X-Api-Key": ""})
    assert response.status_code == 403


def test_health_exempt_with_empty_key(self=None):
    """/health remains accessible even when a wrong/empty key is sent (exempt path)."""
    app, client = _make_app(token="secret")
    
    # Exempt paths bypass auth entirely — empty or wrong key doesn't matter
    response = client.get("/health", headers={"X-Api-Key": ""})
    assert response.status_code == 200
