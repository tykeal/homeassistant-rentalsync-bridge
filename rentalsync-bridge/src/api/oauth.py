# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""OAuth management API endpoints."""

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import KNOWN_PMS_TYPES, get_settings
from src.database import get_db
from src.models.oauth_credential import OAuthCredential
from src.repositories.credential_repository import CredentialRepository
from src.services.oauth_service import OAuthService, OAuthServiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth", tags=["OAuth"])

# Guesty token-request rate limit
GUESTY_MAX_TOKEN_REQUESTS = 5


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
    provider_class: str = Field(description="Provider class name")
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
    pms_type = settings.pms_type

    repo = CredentialRepository(db)
    credential = await repo.get_credential(pms_type)

    if not credential:
        return {
            "configured": False,
            "connected": False,
            "auth_type": None,
            "pms_type": pms_type,
            "token_expires_at": None,
            "token_expired": False,
            "token_requests_remaining": None,
        }

    cred_pms = credential.pms_type or "cloudbeds"
    token_requests_remaining: int | None = None
    if cred_pms == "guesty":
        token_requests_remaining = max(
            0, GUESTY_MAX_TOKEN_REQUESTS - credential.token_request_count
        )

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
    pms_type = request.pms_type or settings.pms_type

    # Validate pms_type against known providers
    if pms_type not in KNOWN_PMS_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown pms_type '{pms_type}'. "
            f"Must be one of: {sorted(KNOWN_PMS_TYPES)}",
        )

    # Provider-specific validation
    if pms_type == "guesty":
        if request.api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Guesty does not support API key authentication",
            )
        if request.refresh_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Guesty does not use refresh tokens",
            )
    # Cloudbeds: require either api_key or access_token
    elif not request.api_key and not request.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either api_key or access_token must be provided",
        )
    # Cloudbeds: warn if access_token without refresh_token
    if pms_type == "cloudbeds" and request.access_token and not request.refresh_token:
        logger.warning(
            "Cloudbeds credential configured with access_token "
            "but no refresh_token; token refresh will fail"
        )

    repo = CredentialRepository(db)
    credential = await repo.get_credential(pms_type)

    if credential:
        # Update existing
        credential.client_id = request.client_id
        credential.client_secret = request.client_secret
        credential.api_key = request.api_key
        credential.access_token = request.access_token
        credential.refresh_token = request.refresh_token
        credential.token_expires_at = request.token_expires_at
        logger.info("Updated existing %s credentials", pms_type)
    else:
        # Create new
        credential = OAuthCredential(client_id=request.client_id, pms_type=pms_type)
        credential.client_secret = request.client_secret
        credential.api_key = request.api_key
        credential.access_token = request.access_token
        credential.refresh_token = request.refresh_token
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

_KNOWN_PROVIDERS: list[dict[str, Any]] = [
    {"pms_type": "cloudbeds", "provider_class": "CloudbedsProvider"},
    {"pms_type": "guesty", "provider_class": "GuestyProvider (pending)"},
]

providers_router = APIRouter(prefix="/api", tags=["Providers"])


@providers_router.get("/providers", response_model=list[ProviderInfo])
async def get_providers() -> list[dict[str, Any]]:
    """Return known provider metadata with credential field defs.

    Includes all known providers even if they are not yet registered
    in the provider registry (e.g. guesty before Phase 4).

    Returns:
        List of provider info dicts for dynamic form rendering.
    """
    providers = [dict(p) for p in _KNOWN_PROVIDERS]
    for p in providers:
        p["credential_fields"] = _CREDENTIAL_FIELDS.get(p["pms_type"], [])
    return providers
