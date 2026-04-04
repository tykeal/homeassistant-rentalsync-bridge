# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared helper for creating PMS providers from credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.providers.registry import create_provider
from src.repositories.credential_repository import CredentialRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.models.oauth_credential import OAuthCredential
    from src.providers.base import PMSProvider


def create_provider_for_credential(
    credential: OAuthCredential,
    session: AsyncSession,
) -> PMSProvider:
    """Create a PMSProvider for the given credential.

    Args:
        credential: OAuth credential with ``pms_type``.
        session: DB session (needed for Guesty credential repo).

    Returns:
        Configured PMSProvider instance.
    """
    pms_type = credential.pms_type

    if pms_type == "guesty":
        cred_repo = CredentialRepository(session)
        return create_provider(
            pms_type,
            credential_repo=cred_repo,
            credential_id=credential.id,
            client_id=credential.client_id,
            client_secret=credential.client_secret,
        )

    # Cloudbeds and any other provider
    return create_provider(
        pms_type,
        access_token=credential.access_token,
        refresh_token=credential.refresh_token,
        api_key=credential.api_key,
    )
