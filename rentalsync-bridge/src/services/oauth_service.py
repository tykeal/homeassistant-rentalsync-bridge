# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""OAuth service for PMS token management."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure provider classes are registered in the registry
import src.providers  # noqa: F401
from src.config import get_settings
from src.models.oauth_credential import OAuthCredential
from src.providers.factory import create_provider_for_credential

logger = logging.getLogger(__name__)

# Cloudbeds OAuth endpoints (kept for backwards-compat default path)
CLOUDBEDS_TOKEN_URL = "https://hotels.cloudbeds.com/api/v1.2/oauth/token"

# Token expiry buffer in seconds (refresh 5 minutes before expiry)
TOKEN_EXPIRY_BUFFER_SECONDS = 300

# HTTP status codes
HTTP_OK = 200


class OAuthServiceError(Exception):
    """Exception raised for OAuth service errors."""

    pass


class OAuthService:
    """Service for managing PMS OAuth tokens.

    Routes token refresh through the provider registry so that each
    provider implementation controls its own token exchange logic.
    Falls back to the legacy Cloudbeds HTTP flow when the provider
    raises ``NotImplementedError``.
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
    ) -> tuple[str, str | None, datetime]:
        """Refresh OAuth access token via the provider registry.

        The method first tries the provider's ``refresh_token``
        implementation.  If the provider raises
        ``NotImplementedError`` (e.g. the Cloudbeds provider),
        we fall back to the original Cloudbeds HTTP refresh.

        Args:
            credential: OAuth credential to refresh.

        Returns:
            Tuple of (access_token, refresh_token | None, expires_at).

        Raises:
            OAuthServiceError: If token refresh fails.
        """
        pms_type = credential.pms_type or "cloudbeds"

        try:
            provider_inst = create_provider_for_credential(credential, self._session)
        except ValueError:
            # Provider not yet registered
            logger.warning(
                "Provider '%s' is not registered; cannot refresh token",
                pms_type,
            )
            msg = (
                f"Provider '{pms_type}' is not yet registered. "
                "Token refresh is unavailable until the provider "
                "is implemented."
            )
            raise OAuthServiceError(msg) from None

        try:
            result = await provider_inst.refresh_token(credential)
            return (
                result.access_token,
                result.refresh_token,
                result.expires_at,
            )
        except NotImplementedError:
            # Provider does not handle refresh — use legacy path
            return await self._cloudbeds_refresh(credential)
        except Exception as e:
            logger.exception("Provider %s token refresh failed", pms_type)
            msg = f"Token refresh failed for {pms_type}: {e}"
            raise OAuthServiceError(msg) from e
        finally:
            if hasattr(provider_inst, "aclose"):
                await provider_inst.aclose()

    async def _cloudbeds_refresh(
        self, credential: OAuthCredential
    ) -> tuple[str, str, datetime]:
        """Legacy Cloudbeds HTTP token refresh.

        Args:
            credential: OAuth credential to refresh.

        Returns:
            Tuple of (access_token, refresh_token, expires_at).

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
                        "client_id": credential.client_id,
                        "client_secret": credential.client_secret,
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
        if refresh_token is not None:
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
