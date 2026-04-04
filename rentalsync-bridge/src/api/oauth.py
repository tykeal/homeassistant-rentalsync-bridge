# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""OAuth management API endpoints."""

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import KNOWN_PMS_TYPES, get_settings
from src.database import get_db
from src.models.oauth_credential import OAuthCredential
from src.providers.registry import get_provider_class
from src.repositories.credential_repository import (
    TOKEN_REQUEST_WINDOW,
    CredentialRepository,
)
from src.services.oauth_service import OAuthService, OAuthServiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth", tags=["OAuth"])

# Guesty token-request rate limit
TOKEN_REQUEST_LIMIT = 5


class OAuthStatusResponse(BaseModel):
    """Response model for OAuth status."""

    configured: bool = Field(description="Whether OAuth credentials are configured")
    connected: bool = Field(description="Whether OAuth connection is active")
    auth_type: str | None = Field(
        default=None, description="Authentication type: 'api_key' or 'oauth'"
    )
    pms_type: str | None = Field(default=None, description="PMS provider type")
    token_expires_at: datetime | None = Field(
        default=None, description="Token expiration time"
    )
    token_expired: bool = Field(default=False, description="Whether token has expired")
    token_requests_remaining: int | None = Field(
        default=None,
        description="Remaining token requests in window (Guesty only)",
    )


class OAuthConfigureRequest(BaseModel):
    """Request model for configuring OAuth credentials."""

    pms_type: str | None = Field(
        default=None,
        description="PMS provider type (cloudbeds or guesty). "
        "Defaults to configured pms_type.",
    )
    client_id: str = Field(min_length=1, description="OAuth client ID")
    client_secret: str = Field(min_length=1, description="OAuth client secret")
    api_key: str | None = Field(
        default=None, description="API key (alternative to OAuth tokens)"
    )
    access_token: str | None = Field(
        default=None, description="OAuth access token (optional if using API key)"
    )
    refresh_token: str | None = Field(
        default=None, description="OAuth refresh token (optional if using API key)"
    )
    token_expires_at: datetime | None = Field(
        default=None, description="Token expiration time"
    )


class OAuthConfigureResponse(BaseModel):
    """Response model for OAuth configuration."""

    success: bool = Field(description="Whether configuration succeeded")
    message: str = Field(description="Status message")


class OAuthRefreshResponse(BaseModel):
    """Response model for token refresh."""

    success: bool = Field(description="Whether refresh succeeded")
    token_expires_at: datetime | None = Field(
        default=None, description="New token expiration time"
    )
    message: str = Field(description="Status message")


class ProviderInfo(BaseModel):
    """Provider metadata for the /api/providers endpoint."""

    pms_type: str = Field(description="Provider identifier")
    provider_class: str | None = Field(description="Provider class name")
    registered: bool = Field(description="Whether provider is registered")
    credential_fields: list[dict[str, str]] = Field(
        description="Required credential fields for dynamic form rendering"
    )


@router.get("/status", response_model=OAuthStatusResponse)
async def get_oauth_status(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get OAuth credential status.

    Returns:
        OAuth configuration and connection status.
    """
    settings = get_settings()
    repo = CredentialRepository(db)

    # Try settings-configured type first, then fall back to any active
    # credential so that UI-configured providers are visible even when
    # the env-var default differs.
    credential = await repo.get_credential(settings.pms_type)
    if not credential:
        credential = await repo.get_active_credential()

    if not credential:
        return {
            "configured": False,
            "connected": False,
            "auth_type": None,
            "pms_type": settings.pms_type,
            "token_expires_at": None,
            "token_expired": False,
            "token_requests_remaining": None,
        }

    cred_pms = credential.pms_type or "cloudbeds"
    token_requests_remaining: int | None = None
    if cred_pms == "guesty":
        remaining = TOKEN_REQUEST_LIMIT
        if credential.token_request_window_start:
            window_start = credential.token_request_window_start
            if window_start.tzinfo is None:
                window_start = window_start.replace(tzinfo=UTC)
            window_expired = datetime.now(UTC) > window_start + TOKEN_REQUEST_WINDOW
            if not window_expired:
                remaining = max(0, TOKEN_REQUEST_LIMIT - credential.token_request_count)
        token_requests_remaining = remaining

    # Determine auth type and connection status
    if credential.has_api_key():
        return {
            "configured": True,
            "connected": True,
            "auth_type": "api_key",
            "pms_type": cred_pms,
            "token_expires_at": None,
            "token_expired": False,
            "token_requests_remaining": token_requests_remaining,
        }

    # OAuth token auth
    token_expired = credential.is_token_expired()
    return {
        "configured": True,
        "connected": not token_expired,
        "auth_type": "oauth",
        "pms_type": cred_pms,
        "token_expires_at": credential.token_expires_at,
        "token_expired": token_expired,
        "token_requests_remaining": token_requests_remaining,
    }


async def _auto_fetch_guesty_token(
    db: AsyncSession,
    credential: OAuthCredential,
) -> None:
    """Try to fetch an initial Guesty access token after configure.

    Logs a warning on failure but does not raise — the credentials
    are already persisted and the token will be retried on the next
    sync cycle.

    Args:
        db: Async database session.
        credential: Newly saved Guesty credential.
    """
    await db.refresh(credential)
    try:
        oauth_service = OAuthService(db)
        await oauth_service.refresh_and_save(credential)
        logger.info("Auto-fetched initial Guesty access token")
    except OAuthServiceError:
        logger.warning("Could not auto-fetch Guesty token; will retry on next sync")


@router.post("/configure", response_model=OAuthConfigureResponse)
async def configure_oauth(
    request: OAuthConfigureRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Configure OAuth credentials.

    Supports two authentication modes:
    1. API Key: Provide client_id, client_secret, and api_key
    2. OAuth: Provide client_id, client_secret, access_token, and refresh_token

    Guesty only supports OAuth (client_credentials); api_key and
    refresh_token are rejected.

    Args:
        request: OAuth credential configuration.
        db: Database session.

    Returns:
        Configuration status.
    """
    settings = get_settings()
    pms_type = (request.pms_type or settings.pms_type).strip().lower()

    # Validate pms_type against known providers
    if pms_type not in KNOWN_PMS_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown pms_type '{pms_type}'. "
            f"Must be one of: {sorted(KNOWN_PMS_TYPES)}",
        )

    # Normalise optional string fields so whitespace-only values are
    # treated as empty.
    api_key = (request.api_key or "").strip()
    refresh_token_val = (request.refresh_token or "").strip()
    access_token_val = (request.access_token or "").strip()

    # Provider-specific validation
    if pms_type == "guesty":
        if api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Guesty does not support API key authentication",
            )
        if refresh_token_val:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Guesty does not use refresh tokens",
            )
    # Cloudbeds: require either api_key or access_token
    elif not api_key and not access_token_val:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either api_key or access_token must be provided",
        )
    # Cloudbeds: warn if access_token without refresh_token
    if pms_type == "cloudbeds" and access_token_val and not refresh_token_val:
        logger.warning(
            "Cloudbeds credential configured with access_token "
            "but no refresh_token; token refresh will fail"
        )

    repo = CredentialRepository(db)
    credential = await repo.get_credential(pms_type)

    if credential:
        # Update existing
        credential.client_id = request.client_id.strip()
        credential.client_secret = request.client_secret.strip()
        credential.api_key = api_key or None
        credential.access_token = access_token_val or None
        credential.refresh_token = refresh_token_val or None
        credential.token_expires_at = request.token_expires_at
        logger.info("Updated existing %s credentials", pms_type)
    else:
        # Create new
        credential = OAuthCredential(
            client_id=request.client_id.strip(), pms_type=pms_type
        )
        credential.client_secret = request.client_secret.strip()
        credential.api_key = api_key or None
        credential.access_token = access_token_val or None
        credential.refresh_token = refresh_token_val or None
        credential.token_expires_at = request.token_expires_at
        db.add(credential)
        logger.info("Created new %s credentials", pms_type)

    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        logger.error("IntegrityError saving %s credentials: %s", pms_type, e)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Credential conflict for '{pms_type}': a duplicate or "
            "constraint violation occurred",
        ) from e

    # Auto-fetch initial token for Guesty (client_credentials flow)
    if pms_type == "guesty":
        await _auto_fetch_guesty_token(db, credential)

    auth_type = "API key" if request.api_key else "OAuth tokens"
    return {
        "success": True,
        "message": f"Credentials configured successfully using {auth_type}",
    }


@router.post("/refresh", response_model=OAuthRefreshResponse)
async def refresh_oauth_token(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Manually refresh OAuth token.

    Args:
        db: Database session.

    Returns:
        Refresh status and new expiration time.

    Raises:
        HTTPException: 400 if no credentials configured or refresh fails.
    """
    settings = get_settings()
    repo = CredentialRepository(db)
    credential = await repo.get_credential(settings.pms_type)
    if not credential:
        credential = await repo.get_active_credential()

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No OAuth credentials configured",
        )

    try:
        oauth_service = OAuthService(db)
        updated_credential = await oauth_service.refresh_and_save(credential)

        logger.info("OAuth token refreshed manually")

        return {
            "success": True,
            "token_expires_at": updated_credential.token_expires_at,
            "message": "Token refreshed successfully",
        }

    except OAuthServiceError as e:
        logger.error("Failed to refresh OAuth token: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Token refresh failed: {e}",
        ) from e


# ------------------------------------------------------------------
# Provider metadata endpoint
# ------------------------------------------------------------------
_CREDENTIAL_FIELDS: dict[str, list[dict[str, str]]] = {
    "cloudbeds": [
        {"name": "client_id", "label": "Client ID", "type": "text"},
        {"name": "client_secret", "label": "Client Secret", "type": "password"},
        {"name": "api_key", "label": "API Key (optional)", "type": "password"},
        {"name": "access_token", "label": "Access Token", "type": "password"},
        {"name": "refresh_token", "label": "Refresh Token", "type": "password"},
    ],
    "guesty": [
        {"name": "client_id", "label": "Client ID", "type": "text"},
        {"name": "client_secret", "label": "Client Secret", "type": "password"},
    ],
}

providers_router = APIRouter(prefix="/api", tags=["Providers"])


@providers_router.get("/providers", response_model=list[ProviderInfo])
async def get_providers() -> list[ProviderInfo]:
    """Return known provider metadata with credential field defs.

    Builds the list from :data:`KNOWN_PMS_TYPES` and checks the
    provider registry so the response reflects actual availability.

    Returns:
        List of ProviderInfo objects for dynamic form rendering.
    """
    providers: list[ProviderInfo] = []
    for pms_type in sorted(KNOWN_PMS_TYPES):
        try:
            cls = get_provider_class(pms_type)
            providers.append(
                ProviderInfo(
                    pms_type=pms_type,
                    provider_class=cls.__name__,
                    registered=True,
                    credential_fields=_CREDENTIAL_FIELDS.get(pms_type, []),
                )
            )
        except ValueError:
            providers.append(
                ProviderInfo(
                    pms_type=pms_type,
                    provider_class=None,
                    registered=False,
                    credential_fields=_CREDENTIAL_FIELDS.get(pms_type, []),
                )
            )
    return providers
