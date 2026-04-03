# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty OAuth2 token manager with caching and rate limiting."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx

from src.providers.base import (
    PMSAuthenticationError,
    PMSConnectionError,
    TokenRateLimitError,
    TokenResult,
)
from src.repositories.credential_repository import (
    TOKEN_REQUEST_WINDOW,
    CredentialRepository,
)

logger = logging.getLogger(__name__)

GUESTY_TOKEN_URL = "https://open-api.guesty.com/oauth2/token"
TOKEN_VALIDITY = timedelta(hours=24)
TOKEN_REQUEST_LIMIT = 5
TOKEN_WARN_THRESHOLD = 3


class GuestyTokenManager:
    """Manage Guesty OAuth2 client-credentials tokens.

    Provides token caching with 24-hour validity and request counting
    per rolling window.  Warns at the 4th request, allows 5th with
    warning, and defers (raises ``TokenRateLimitError``) at the 6th.

    Uses ``CredentialRepository`` for persistence of tokens and request
    counts.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        credential_repo: CredentialRepository,
        credential_id: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize GuestyTokenManager.

        Args:
            client_id: Guesty OAuth2 client ID.
            client_secret: Guesty OAuth2 client secret.
            credential_repo: Repository for credential persistence.
            credential_id: Database ID of the credential row.
            http_client: Optional httpx client for token requests.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._repo = credential_repo
        self._credential_id = credential_id
        self._http_client = http_client

        # In-memory cache
        self._cached_token: str | None = None
        self._cached_expires_at: datetime | None = None

        # Serialise token refresh so concurrent callers don't all hit
        # _request_token() and burn through the 5/day limit.
        self._lock = asyncio.Lock()

    @property
    def cached_token(self) -> str | None:
        """Return the currently cached token, if any."""
        return self._cached_token

    @property
    def cached_expires_at(self) -> datetime | None:
        """Return the expiry time of the cached token, if any."""
        return self._cached_expires_at

    def _is_cache_valid(self) -> bool:
        """Check whether the in-memory token cache is still valid.

        Returns:
            True if a cached token exists and has not expired.
        """
        if self._cached_token is None or self._cached_expires_at is None:
            return False
        return datetime.now(UTC) < self._cached_expires_at

    async def _check_rate_limit(self) -> None:
        """Check and enforce the token request rate limit.

        The Guesty API limits token requests to 5 per 24-hour window.
        This method:
        - Allows requests 1-3 silently
        - Warns at the 4th request
        - Allows the 5th with a warning
        - Raises ``TokenRateLimitError`` at 6 or more

        Raises:
            TokenRateLimitError: If the rate limit would be exceeded.
        """
        count = await self._repo.get_token_request_count(
            self._credential_id,
        )

        # pms_type has a unique constraint (Phase 3 migration), so
        # get_credential("guesty") always returns the same row as a
        # lookup by self._credential_id.
        credential = await self._repo.get_credential("guesty")
        window_start = credential.token_request_window_start if credential else None

        # If the window has expired, the next increment will reset the count,
        # so treat this as an allowed request regardless of the stored count.
        if window_start is not None:
            window_age = datetime.now(UTC) - window_start.replace(tzinfo=UTC)
            if window_age >= TOKEN_REQUEST_WINDOW:
                return

        if count >= TOKEN_REQUEST_LIMIT:
            reset_at = None
            if window_start is not None:
                reset_at = window_start + TOKEN_REQUEST_WINDOW
            msg = (
                f"Token request rate limit exceeded "
                f"({count}/{TOKEN_REQUEST_LIMIT} in current window). "
                f"Deferring token request."
            )
            logger.error(msg)
            raise TokenRateLimitError(msg, reset_at=reset_at)

        if count >= TOKEN_WARN_THRESHOLD:
            remaining = TOKEN_REQUEST_LIMIT - count
            logger.warning(
                "Guesty token request rate limit warning: "
                "%d/%d used, %d remaining in current window",
                count,
                TOKEN_REQUEST_LIMIT,
                remaining,
            )

    async def get_token(self) -> str:
        """Get a valid Guesty access token.

        Returns a cached token if still valid.  Otherwise, checks the
        rate limit and requests a new token from the Guesty API.

        Returns:
            A valid access token string.

        Raises:
            TokenRateLimitError: If the rate limit would be exceeded.
            PMSAuthenticationError: If token request fails (bad creds).
            PMSConnectionError: If unable to reach Guesty API.
        """
        # Quick check without lock
        if self._is_cache_valid():
            return self._cached_token  # type: ignore[return-value]

        async with self._lock:
            # Re-check inside lock (double-checked locking)
            if self._is_cache_valid():
                return self._cached_token  # type: ignore[return-value]

            # Try loading a still-valid token from the database.
            # pms_type has a unique constraint (Phase 3 migration), so
            # get_credential("guesty") always returns the same row as a
            # lookup by self._credential_id.
            credential = await self._repo.get_credential("guesty")
            if credential and credential.access_token and credential.token_expires_at:
                expires_at = credential.token_expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if datetime.now(UTC) < expires_at:
                    self._cached_token = credential.access_token
                    self._cached_expires_at = expires_at
                    return self._cached_token

            await self._check_rate_limit()

            result = await self._request_token()

            # Persist to database
            await self._repo.update_token(
                self._credential_id,
                result.access_token,
                result.expires_at,
            )
            await self._repo.increment_token_request_count(
                self._credential_id,
            )

            # Update in-memory cache
            self._cached_token = result.access_token
            self._cached_expires_at = result.expires_at

            return result.access_token

    async def _request_token(self) -> TokenResult:
        """Request a new token from the Guesty OAuth2 endpoint.

        Returns:
            TokenResult with the new access token and expiry.

        Raises:
            PMSAuthenticationError: On 401/403 or invalid credentials.
            PMSConnectionError: On network-level failures.
        """
        client = self._http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
        )
        owns_client = self._http_client is None

        try:
            response = await client.post(
                GUESTY_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "scope": "open-api",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        except httpx.HTTPError as exc:
            msg = f"Failed to connect to Guesty OAuth endpoint: {exc}"
            raise PMSConnectionError(msg) from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code in (401, 403):
            msg = (
                f"Guesty authentication failed "
                f"(HTTP {response.status_code}): {response.text}"
            )
            raise PMSAuthenticationError(msg)

        if response.status_code != 200:  # noqa: PLR2004
            msg = (
                f"Guesty token request failed "
                f"(HTTP {response.status_code}): {response.text}"
            )
            raise PMSAuthenticationError(msg)

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            msg = "Token response missing access_token"
            raise PMSAuthenticationError(msg)

        expires_in = data.get("expires_in", 86400)
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            expires_in = 86400  # Default 24h
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

        return TokenResult(
            access_token=access_token,
            refresh_token=None,
            expires_at=expires_at,
        )

    def invalidate_cache(self) -> None:
        """Clear the in-memory token cache.

        Forces the next ``get_token()`` call to request a fresh token.
        """
        self._cached_token = None
        self._cached_expires_at = None
