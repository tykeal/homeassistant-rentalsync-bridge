# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Settings API endpoints for runtime configuration."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings as get_app_settings
from src.database import get_db
from src.models.system_settings import DEFAULT_SYNC_INTERVAL_MINUTES, SystemSettings
from src.services.scheduler import get_scheduler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class SyncIntervalRequest(BaseModel):
    """Request model for updating sync interval."""

    interval_minutes: int = Field(
        ge=1,
        le=60,
        description="Sync interval in minutes (1-60)",
    )


class SyncIntervalResponse(BaseModel):
    """Response model for sync interval."""

    interval_minutes: int = Field(description="Current sync interval in minutes")
    message: str = Field(description="Status message")


class SettingsResponse(BaseModel):
    """Response model for all settings."""

    sync_interval_minutes: int = Field(description="Sync interval in minutes")
    ical_base_url: str = Field(description="Base URL for iCal endpoints")


@router.get("", response_model=SettingsResponse)
async def get_settings(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SettingsResponse:
    """Get current system settings.

    Returns:
        Current system settings including iCal base URL.
    """
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.settings_key == "default")
    )
    settings = result.scalar_one_or_none()

    sync_interval = (
        settings.sync_interval_minutes if settings else DEFAULT_SYNC_INTERVAL_MINUTES
    )

    # Determine iCal base URL for the frontend to use when copying calendar URLs.
    # Two modes:
    # 1. HA add-on mode: ICAL_BASE_URL is set via environment (e.g., http://hostname:8099)
    #    This provides the internal container hostname for direct network access.
    # 2. Standalone mode: Falls back to request.base_url (e.g., http://localhost:8099/)
    #    The trailing slash is stripped to ensure consistent URL construction
    #    (the iCal path will be appended as /ical/...).
    app_settings = get_app_settings()
    ical_base_url = app_settings.ical_base_url or str(request.base_url).rstrip("/")

    return SettingsResponse(
        sync_interval_minutes=sync_interval,
        ical_base_url=ical_base_url,
    )


@router.put("/sync-interval", response_model=SyncIntervalResponse)
async def update_sync_interval(
    request: SyncIntervalRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SyncIntervalResponse:
    """Update the sync interval.

    Updates both the database and the running scheduler dynamically.

    Args:
        request: New sync interval settings.
        db: Database session.

    Returns:
        Updated sync interval confirmation.
    """
    # Get or create settings
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.settings_key == "default")
    )
    settings = result.scalar_one_or_none()

    if not settings:
        # Create default settings if they don't exist
        settings = SystemSettings(
            id=1,
            sync_interval_minutes=request.interval_minutes,
            settings_key="default",
        )
        db.add(settings)
    else:
        settings.sync_interval_minutes = request.interval_minutes

    await db.commit()
    await db.refresh(settings)

    # Update the running scheduler dynamically
    scheduler = get_scheduler()
    if scheduler and scheduler.is_running:
        scheduler.update_sync_interval(request.interval_minutes)
        logger.info("Sync interval updated to %d minutes", request.interval_minutes)
        message = f"Sync interval updated to {request.interval_minutes} minutes"
    else:
        logger.warning("Scheduler not running, interval will apply on next start")
        message = (
            f"Sync interval saved as {request.interval_minutes} minutes "
            "(will apply on next scheduler start)"
        )

    return SyncIntervalResponse(
        interval_minutes=settings.sync_interval_minutes,
        message=message,
    )
