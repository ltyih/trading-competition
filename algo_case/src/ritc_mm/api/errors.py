"""Custom exception hierarchy for the RIT API client.

Every exception carries structured context so callers can log/handle failures
with full diagnostics without inspecting raw HTTP details.
"""

from __future__ import annotations


class ApiError(Exception):
    """Base class for all API errors."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(ApiError):
    """Raised on HTTP 401 — invalid or missing API key."""

    def __init__(self, message: str = "Authentication failed (HTTP 401)") -> None:
        super().__init__(message, status_code=401)


class RateLimitError(ApiError):
    """Raised on HTTP 429 — rate limit exceeded.

    Attributes
    ----------
    retry_after : float
        Seconds the server asked us to wait before retrying.
    """

    def __init__(self, retry_after: float, message: str | None = None) -> None:
        super().__init__(
            message or f"Rate limited; retry after {retry_after:.3f}s",
            status_code=429,
        )
        self.retry_after = retry_after


class ServerError(ApiError):
    """Raised on HTTP 5xx — server-side failures."""

    def __init__(self, status_code: int, message: str | None = None) -> None:
        super().__init__(
            message or f"Server error (HTTP {status_code})",
            status_code=status_code,
        )


class ConnectionFailure(ApiError):
    """Raised when the client cannot reach the API at all (timeout / DNS / refused)."""

    def __init__(self, message: str = "Unable to connect to RIT API") -> None:
        super().__init__(message, status_code=None)


class EndpointNotFoundError(ApiError):
    """Raised on HTTP 404 — endpoint or resource does not exist."""

    def __init__(self, message: str = "Endpoint not found (HTTP 404)") -> None:
        super().__init__(message, status_code=404)


class UnexpectedStatusError(ApiError):
    """Raised for any status code not otherwise classified."""

    def __init__(self, status_code: int, body: str = "") -> None:
        super().__init__(
            f"Unexpected HTTP {status_code}: {body[:200]}",
            status_code=status_code,
        )
