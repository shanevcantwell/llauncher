"""Authentication middleware for the llauncher agent service.

Provides API key-based authentication via the X-Api-Key header,
with exemptions for health check and OpenAPI documentation endpoints.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from fastapi import Request


# Paths that skip authentication regardless of token configuration
_AUTH_EXEMPT_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces API key authentication.

    Checks for the ``X-Api-Key`` header on every request and returns:
    * **401** if the header is missing,
    * **403** if the header value does not match the expected token.

    Skips authentication for exempt paths (/health, /docs, //openapi.json, /redoc).
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

        if api_key is None or api_key != self.expected_token:
            # 401 when header absent (authentication required)
            # 403 when header present but wrong/empty (credentials provided, access denied)
            status_code = 401 if api_key is None else 403
            return JSONResponse(
                status_code=status_code,
                content={"detail": "Authentication required"},
            )

        response = await call_next(request)
        return response
