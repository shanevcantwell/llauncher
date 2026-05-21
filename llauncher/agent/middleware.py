"""Middleware for the llauncher agent service.

Provides:

* :class:`AuthenticationMiddleware` — API key auth via the ``X-Api-Key``
  header with exemptions for health/OpenAPI paths.
* :class:`BodySizeLimitMiddleware` — defense-in-depth cap on inbound
  HTTP request body size (security plan §3 control C3 / issue #78).
"""

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from fastapi import Request


# Paths that skip authentication regardless of token configuration
_AUTH_EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})


# Maximum allowed inbound HTTP request body size, in bytes.
# Security plan §3 C3 / issue #78: defense-in-depth against accidental or
# malicious oversize payloads. 1 MiB comfortably accommodates every legitimate
# agent payload (model configs, swap/start requests are all small JSON
# documents) while bounding worst-case memory pressure.
MAX_REQUEST_BODY_BYTES: int = 1024 * 1024  # 1 MiB


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces API key authentication.

    Checks for the ``X-Api-Key`` header on every request and returns:
    * **401** if the header is missing,
    * **403** if the header value does not match the expected token.

    Skips authentication for exempt paths (/health, /docs, /openapi.json, /redoc).
    """

    def __init__(self, app, expected_token: str):
        """Initialize the middleware.

        Args:
            app: The FastAPI application to wrap.
            expected_token: The API key value that will be accepted.
        """
        super().__init__(app)
        self.expected_token = expected_token

    async def dispatch(self, request: Request, call_next):
        """Process the request and enforce authentication.

        Args:
            request: The incoming FastAPI request.
            call_next: Async callable to invoke the next handler in the chain.

        Returns:
            A JSONResponse with 401/403 if authentication fails,
            or the response from the next handler on success.
        """
        path = request.url.path

        if path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        api_key = request.headers.get("X-Api-Key")

        if api_key is None or not hmac.compare_digest(api_key, self.expected_token):
            # 401 when header absent (authentication required)
            # 403 when header present but wrong/empty (credentials provided, access denied)
            status_code = 401 if api_key is None else 403
            return JSONResponse(
                status_code=status_code,
                content={"detail": "Authentication required"},
            )

        response = await call_next(request)
        return response


class BodySizeLimitMiddleware:
    """Pure-ASGI middleware that rejects oversize HTTP request bodies.

    Rejects with HTTP 413 (Payload Too Large) when an inbound HTTP request
    body exceeds :data:`MAX_REQUEST_BODY_BYTES`. Implemented at the ASGI
    layer (not :class:`BaseHTTPMiddleware`) so the body never gets fully
    buffered into memory before the decision is made.

    Two enforcement paths:

    1. **Fast path** — if the client advertises a ``Content-Length`` header
       exceeding the cap, reject before reading any body bytes.
    2. **Streaming path** — otherwise, accumulate ``http.request`` byte
       counts as they arrive and reject as soon as the total crosses the
       cap. This covers chunked encoding and missing/lying
       Content-Length headers.

    Non-HTTP scopes (lifespan, websocket) and HTTP methods that do not
    carry a body in the usual sense still flow through the size check
    uniformly: a GET with an honestly-empty body trivially passes.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        """Initialize the middleware.

        Args:
            app: The downstream ASGI application.
            max_bytes: Maximum allowed body size in bytes. Defaults to
                :data:`MAX_REQUEST_BODY_BYTES` (1 MiB).
        """
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Fast path: trust an honest Content-Length header if it exceeds
        # the cap. (If it lies low, the streaming path still catches it.)
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except (TypeError, ValueError):
                    break
                if declared > self.max_bytes:
                    await _send_413(send)
                    return
                break

        bytes_seen = 0
        max_bytes = self.max_bytes
        rejected = False

        async def limited_receive() -> Message:
            nonlocal bytes_seen, rejected
            message = await receive()
            if message["type"] != "http.request":
                return message
            body = message.get("body", b"") or b""
            bytes_seen += len(body)
            if bytes_seen > max_bytes:
                rejected = True
                # Surface as a sentinel disconnect so the downstream app
                # stops reading; the 413 response is sent below.
                return {"type": "http.disconnect"}
            return message

        # Wrap send so that once we've decided to reject, we suppress any
        # response the downstream app might have started emitting on the
        # disconnect signal.
        response_started = False

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if rejected:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        await self.app(scope, limited_receive, guarded_send)

        if rejected and not response_started:
            await _send_413(send)


async def _send_413(send: Send) -> None:
    """Emit a minimal HTTP 413 JSON response via raw ASGI ``send``."""
    body = b'{"detail":"Request body too large"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})
