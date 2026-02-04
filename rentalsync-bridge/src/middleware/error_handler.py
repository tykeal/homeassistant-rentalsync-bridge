# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Global error handling middleware for consistent error responses."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Middleware for consistent error handling and logging.

    Catches unhandled exceptions and converts them to appropriate
    JSON responses without exposing sensitive error details.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Process request and handle any exceptions.

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware or route handler.

        Returns:
            HTTP response.
        """
        try:
            return await call_next(request)
        except HTTPException:
            # Let FastAPI handle HTTP exceptions normally
            raise
        except Exception:
            # Log the full exception for debugging
            logger.exception(
                "Unhandled exception for %s %s", request.method, request.url.path
            )

            # Return generic error response without sensitive details
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "An internal error occurred. Please try again later.",
                    "type": "internal_error",
                },
            )


def create_error_response(
    status_code: int,
    message: str,
    error_type: str = "error",
) -> JSONResponse:
    """Create a standardized error response.

    Args:
        status_code: HTTP status code.
        message: User-facing error message.
        error_type: Error type identifier.

    Returns:
        JSONResponse with error details.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": message,
            "type": error_type,
        },
    )


def service_unavailable_response(
    message: str = "Service temporarily unavailable",
    retry_after: int | None = None,
) -> JSONResponse:
    """Create a 503 Service Unavailable response.

    Args:
        message: User-facing error message.
        retry_after: Seconds until retry (optional).

    Returns:
        JSONResponse with 503 status and optional Retry-After header.
    """
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": message,
            "type": "service_unavailable",
        },
        headers=headers or None,
    )
