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
    access_token: str = Field(min_length=1, description="OAuth access token")
    refresh_token: str = Field(min_length=1, description="OAuth refresh token")
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
            "token_expires_at": None,
            "token_expired": False,
        }

    token_expired = credential.is_token_expired()

    return {
        "configured": True,
        "connected": not token_expired,
        "token_expires_at": credential.token_expires_at,
        "token_expired": token_expired,
    }


@router.post("/configure", response_model=OAuthConfigureResponse)
async def configure_oauth(
    request: OAuthConfigureRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Configure OAuth credentials.

    Args:
        request: OAuth credential configuration.
        db: Database session.

    Returns:
        Configuration status.
    """
    # Check for existing credential
    result = await db.execute(select(OAuthCredential).limit(1))
    credential = result.scalar_one_or_none()

    if credential:
        # Update existing
        credential.client_id = request.client_id
        credential.client_secret = request.client_secret
        credential.access_token = request.access_token
        credential.refresh_token = request.refresh_token
        credential.token_expires_at = request.token_expires_at
        logger.info("Updated existing OAuth credentials")
    else:
        # Create new
        credential = OAuthCredential(client_id=request.client_id)
        credential.client_secret = request.client_secret
        credential.access_token = request.access_token
        credential.refresh_token = request.refresh_token
        credential.token_expires_at = request.token_expires_at
        db.add(credential)
        logger.info("Created new OAuth credentials")

    await db.commit()

    return {
        "success": True,
        "message": "OAuth credentials configured successfully",
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
