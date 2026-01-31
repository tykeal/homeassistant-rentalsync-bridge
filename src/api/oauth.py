# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""OAuth management API endpoints."""

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models.oauth_credential import OAuthCredential
from src.services.oauth_service import OAuthService, OAuthServiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/oauth", tags=["OAuth"])


class OAuthStatusResponse(BaseModel):
    """Response model for OAuth status."""

    configured: bool = Field(description="Whether OAuth credentials are configured")
    connected: bool = Field(description="Whether OAuth connection is active")
    auth_type: str | None = Field(
        default=None, description="Authentication type: 'api_key' or 'oauth'"
    )
    token_expires_at: datetime | None = Field(
        default=None, description="Token expiration time"
    )
    token_expired: bool = Field(default=False, description="Whether token has expired")


class OAuthConfigureRequest(BaseModel):
    """Request model for configuring OAuth credentials."""

    client_id: str = Field(min_length=1, description="Cloudbeds OAuth client ID")
    client_secret: str = Field(
        min_length=1, description="Cloudbeds OAuth client secret"
    )
    api_key: str | None = Field(
        default=None, description="Cloudbeds API key (alternative to OAuth tokens)"
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


@router.get("/status", response_model=OAuthStatusResponse)
async def get_oauth_status(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get OAuth credential status.

    Returns:
        OAuth configuration and connection status.
    """
    result = await db.execute(select(OAuthCredential).limit(1))
    credential = result.scalar_one_or_none()

    if not credential:
        return {
            "configured": False,
            "connected": False,
            "auth_type": None,
            "token_expires_at": None,
            "token_expired": False,
        }

    # Determine auth type and connection status
    if credential.has_api_key():
        # API key auth - always connected if configured
        return {
            "configured": True,
            "connected": True,
            "auth_type": "api_key",
            "token_expires_at": None,
            "token_expired": False,
        }

    # OAuth token auth
    token_expired = credential.is_token_expired()
    return {
        "configured": True,
        "connected": not token_expired,
        "auth_type": "oauth",
        "token_expires_at": credential.token_expires_at,
        "token_expired": token_expired,
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

    Args:
        request: OAuth credential configuration.
        db: Database session.

    Returns:
        Configuration status.
    """
    # Validate that either api_key or tokens are provided
    if not request.api_key and not request.access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either api_key or access_token must be provided",
        )

    # Check for existing credential
    result = await db.execute(select(OAuthCredential).limit(1))
    credential = result.scalar_one_or_none()

    if credential:
        # Update existing
        credential.client_id = request.client_id
        credential.client_secret = request.client_secret
        credential.api_key = request.api_key
        credential.access_token = request.access_token
        credential.refresh_token = request.refresh_token
        credential.token_expires_at = request.token_expires_at
        logger.info("Updated existing OAuth credentials")
    else:
        # Create new
        credential = OAuthCredential(client_id=request.client_id)
        credential.client_secret = request.client_secret
        credential.api_key = request.api_key
        credential.access_token = request.access_token
        credential.refresh_token = request.refresh_token
        credential.token_expires_at = request.token_expires_at
        db.add(credential)
        logger.info("Created new OAuth credentials")

    await db.commit()

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
    result = await db.execute(select(OAuthCredential).limit(1))
    credential = result.scalar_one_or_none()

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
