# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""OAuth service for Cloudbeds token management."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.oauth_credential import OAuthCredential

logger = logging.getLogger(__name__)

# Cloudbeds OAuth endpoints
CLOUDBEDS_TOKEN_URL = "https://hotels.cloudbeds.com/api/v1.2/oauth/token"

# Token expiry buffer in seconds (refresh 5 minutes before expiry)
TOKEN_EXPIRY_BUFFER_SECONDS = 300

# HTTP status codes
HTTP_OK = 200


class OAuthServiceError(Exception):
    """Exception raised for OAuth service errors."""

    pass


class OAuthService:
    """Service for managing Cloudbeds OAuth tokens.

    Handles token refresh and storage of OAuth credentials.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize OAuth service.

        Args:
            session: Async database session for credential storage.
        """
        self._session = session
        self._settings = get_settings()

    async def refresh_token(
        self, credential: OAuthCredential
    ) -> tuple[str, str, datetime]:
        """Refresh OAuth access token.

        Args:
            credential: OAuth credential to refresh.

        Returns:
            Tuple of (new_access_token, new_refresh_token, expires_at).

        Raises:
            OAuthServiceError: If token refresh fails.
        """
        if not credential.refresh_token:
            msg = "No refresh token available"
            raise OAuthServiceError(msg)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    CLOUDBEDS_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self._settings.cloudbeds_client_id,
                        "client_secret": self._settings.cloudbeds_client_secret,
                        "refresh_token": credential.refresh_token,
                    },
                    timeout=30.0,
                )

                if response.status_code != HTTP_OK:
                    logger.error(
                        "Token refresh failed with status %d: %s",
                        response.status_code,
                        response.text,
                    )
                    msg = f"Token refresh failed: {response.status_code}"
                    raise OAuthServiceError(msg)

                data = response.json()
                return self._parse_token_response(data)

        except httpx.RequestError as e:
            logger.exception("HTTP error during token refresh")
            msg = f"HTTP error: {e}"
            raise OAuthServiceError(msg) from e

    async def refresh_and_save(self, credential: OAuthCredential) -> OAuthCredential:
        """Refresh token and save to database.

        Args:
            credential: OAuth credential to refresh and save.

        Returns:
            Updated OAuth credential.

        Raises:
            OAuthServiceError: If token refresh fails.
        """
        access_token, refresh_token, expires_at = await self.refresh_token(credential)

        credential.access_token = access_token
        credential.refresh_token = refresh_token
        credential.token_expires_at = expires_at

        await self._session.commit()
        await self._session.refresh(credential)

        logger.info("OAuth token refreshed and saved successfully")
        return credential

    def should_refresh(self, credential: OAuthCredential) -> bool:
        """Check if token should be refreshed.

        Args:
            credential: OAuth credential to check.

        Returns:
            True if token should be refreshed.
        """
        if credential.is_token_expired():
            return True

        # Also refresh if within buffer period
        if credential.token_expires_at:
            buffer_time = datetime.now(UTC) + timedelta(
                seconds=TOKEN_EXPIRY_BUFFER_SECONDS
            )
            return credential.token_expires_at <= buffer_time

        return False

    def _parse_token_response(self, data: dict[str, Any]) -> tuple[str, str, datetime]:
        """Parse token response from Cloudbeds.

        Args:
            data: JSON response from token endpoint.

        Returns:
            Tuple of (access_token, refresh_token, expires_at).

        Raises:
            OAuthServiceError: If response is invalid.
        """
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in", 3600)

        if not access_token:
            msg = "No access_token in response"
            raise OAuthServiceError(msg)

        if not refresh_token:
            msg = "No refresh_token in response"
            raise OAuthServiceError(msg)

        expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))

        return access_token, refresh_token, expires_at
