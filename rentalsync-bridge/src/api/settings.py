# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Settings API endpoints for runtime configuration."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


@router.get("", response_model=SettingsResponse)
async def get_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SettingsResponse:
    """Get current system settings.

    Returns:
        Current system settings.
    """
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.settings_key == "default")
    )
    settings = result.scalar_one_or_none()

    if settings:
        return SettingsResponse(sync_interval_minutes=settings.sync_interval_minutes)

    return SettingsResponse(sync_interval_minutes=DEFAULT_SYNC_INTERVAL_MINUTES)


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
