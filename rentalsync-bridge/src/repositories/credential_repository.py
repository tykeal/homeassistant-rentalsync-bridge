# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Provider-aware credential repository for OAuth credentials."""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.oauth_credential import OAuthCredential

logger = logging.getLogger(__name__)


class CredentialRepository:
    """Provider-aware CRUD operations for OAuth credentials.

    Each PMS provider stores a separate credential row keyed by
    ``pms_type``.  This repository centralises all credential
    lookups so that callers never need to craft raw SQL.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository.

        Args:
            session: Async database session.
        """
        self._session = session

    async def get_credential(self, pms_type: str) -> OAuthCredential | None:
        """Fetch the credential for a given provider type.

        Args:
            pms_type: Provider identifier (e.g. "cloudbeds", "guesty").

        Returns:
            The matching credential, or ``None`` if not configured.
        """
        result = await self._session.execute(
            select(OAuthCredential).where(OAuthCredential.pms_type == pms_type)
        )
        return result.scalar_one_or_none()

    async def save_credential(self, credential: OAuthCredential) -> OAuthCredential:
        """Add or merge a credential into the session.

        Args:
            credential: Credential to persist.

        Returns:
            The managed credential instance.
        """
        merged = await self._session.merge(credential)
        await self._session.flush()
        return merged

    async def update_token(
        self,
        credential_id: int,
        access_token: str,
        expires_at: datetime,
    ) -> None:
        """Update just the access token and expiry for a credential.

        Args:
            credential_id: Primary key of the credential row.
            access_token: New access token value (will be encrypted).
            expires_at: New expiry timestamp.
        """
        result = await self._session.execute(
            select(OAuthCredential).where(OAuthCredential.id == credential_id)
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            msg = f"Credential {credential_id} not found"
            raise ValueError(msg)
        credential.access_token = access_token
        credential.token_expires_at = expires_at
        await self._session.flush()

    async def get_token_request_count(self, credential_id: int) -> int:
        """Return the current token request count.

        Args:
            credential_id: Primary key of the credential row.

        Returns:
            Current token request count.
        """
        result = await self._session.execute(
            select(OAuthCredential).where(OAuthCredential.id == credential_id)
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            msg = f"Credential {credential_id} not found"
            raise ValueError(msg)
        return credential.token_request_count

    async def increment_token_request_count(self, credential_id: int) -> None:
        """Increment the token request counter by one.

        If no window start has been recorded yet, it is initialised
        to the current UTC time.

        Args:
            credential_id: Primary key of the credential row.
        """
        result = await self._session.execute(
            select(OAuthCredential).where(OAuthCredential.id == credential_id)
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            msg = f"Credential {credential_id} not found"
            raise ValueError(msg)
        credential.token_request_count += 1
        if credential.token_request_window_start is None:
            credential.token_request_window_start = datetime.now(UTC)
        await self._session.flush()

    async def reset_token_request_window(self, credential_id: int) -> None:
        """Reset the token request counter and window start.

        Args:
            credential_id: Primary key of the credential row.
        """
        result = await self._session.execute(
            select(OAuthCredential).where(OAuthCredential.id == credential_id)
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            msg = f"Credential {credential_id} not found"
            raise ValueError(msg)
        credential.token_request_count = 0
        credential.token_request_window_start = None
        await self._session.flush()
