# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Authentication middleware for Home Assistant Ingress integration."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from src.config import get_settings

logger = logging.getLogger(__name__)

# Home Assistant Ingress authentication header
HA_INGRESS_HEADER = "X-Ingress-Path"
HA_AUTHENTICATED_HEADER = "X-Hass-User-Id"

# Paths that don't require authentication
PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/ical",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)


def is_public_path(path: str) -> bool:
    """Check if a path is public (no auth required).

    Args:
        path: Request path to check.

    Returns:
        True if path is public.
    """
    # Exact matches
    if path in PUBLIC_PATHS:
        return True

    # Prefix matches for iCal endpoints
    return path.startswith("/ical/")


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce Home Assistant authentication.

    In standalone mode, authentication is bypassed for development.
    In production (addon mode), requests must include HA Ingress headers.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Process request and enforce authentication.

        Args:
            request: Incoming HTTP request.
            call_next: Next middleware or route handler.

        Returns:
            HTTP response.

        Raises:
            HTTPException: If authentication fails.
        """
        settings = get_settings()
        path = request.url.path

        # Public paths don't require authentication
        if is_public_path(path):
            return await call_next(request)

        # Standalone mode bypasses authentication
        if settings.standalone_mode:
            logger.debug("Standalone mode: bypassing authentication for %s", path)
            return await call_next(request)

        # Check for Home Assistant authentication
        user_id = request.headers.get(HA_AUTHENTICATED_HEADER)
        if not user_id:
            logger.warning("Unauthorized access attempt to %s", path)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Home Assistant"},
            )

        # Store user ID in request state for later use
        request.state.user_id = user_id
        logger.debug("Authenticated request from user %s to %s", user_id, path)

        return await call_next(request)


def get_current_user(request: Request) -> str | None:
    """Get the current authenticated user ID from request.

    Args:
        request: Current HTTP request.

    Returns:
        User ID string or None if not authenticated.
    """
    return getattr(request.state, "user_id", None)
