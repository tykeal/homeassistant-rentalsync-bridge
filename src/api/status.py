# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""System status API endpoint."""

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.models.booking import Booking
from src.models.listing import Listing
from src.models.oauth_credential import OAuthCredential

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Status"])

# Application version
APP_VERSION = "0.1.0"


class ListingsStatus(BaseModel):
    """Listings status information."""

    enabled: int = Field(description="Number of enabled listings")
    total: int = Field(description="Total number of listings")


class SyncStatus(BaseModel):
    """Sync status information."""

    last_sync: datetime | None = Field(default=None, description="Last sync timestamp")
    is_running: bool = Field(default=False, description="Whether sync is running")


class OAuthStatus(BaseModel):
    """OAuth connection status."""

    configured: bool = Field(description="Whether OAuth is configured")
    connected: bool = Field(description="Whether connection is active")


class StatusResponse(BaseModel):
    """Response model for system status."""

    status: str = Field(description="Overall system status")
    version: str = Field(description="Application version")
    timestamp: datetime = Field(description="Current server time")
    oauth: OAuthStatus = Field(description="OAuth status")
    sync: SyncStatus = Field(description="Sync status")
    listings: ListingsStatus = Field(description="Listings status")
    bookings_count: int = Field(description="Total number of bookings")


@router.get("/status", response_model=StatusResponse)
async def get_system_status(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get system status.

    Returns:
        Comprehensive system status including OAuth, sync, and data counts.
    """
    # Get OAuth status
    cred_result = await db.execute(select(OAuthCredential).limit(1))
    credential = cred_result.scalar_one_or_none()

    oauth_configured = credential is not None
    oauth_connected = credential is not None and not credential.is_token_expired()

    # Get listings counts (enabled and total)
    enabled_result = await db.execute(
        select(func.count(Listing.id)).where(Listing.enabled == True)  # noqa: E712
    )
    enabled_count = enabled_result.scalar() or 0

    total_result = await db.execute(select(func.count(Listing.id)))
    total_count = total_result.scalar() or 0

    # Get bookings count
    bookings_result = await db.execute(select(func.count(Booking.id)))
    bookings_count = bookings_result.scalar() or 0

    # Get last sync time from most recent booking fetch
    last_sync_result = await db.execute(select(func.max(Booking.last_fetched_at)))
    last_sync_at = last_sync_result.scalar()

    # Determine overall status
    if not oauth_configured:
        overall_status = "unconfigured"
    elif not oauth_connected:
        overall_status = "disconnected"
    else:
        overall_status = "healthy"

    return {
        "status": overall_status,
        "version": APP_VERSION,
        "timestamp": datetime.now(UTC),
        "oauth": {
            "configured": oauth_configured,
            "connected": oauth_connected,
        },
        "sync": {
            "last_sync": last_sync_at,
            "is_running": False,  # TODO: Check scheduler status
        },
        "listings": {
            "enabled": enabled_count,
            "total": total_count,
        },
        "bookings_count": bookings_count,
    }
