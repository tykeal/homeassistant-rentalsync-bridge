# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Room management API endpoints."""

import logging
import re
from datetime import UTC
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.repositories.room_repository import RoomRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rooms", tags=["Rooms"])

# Valid slug pattern: must start/end with alphanumeric, allows hyphens in middle
# Single character slugs like "a" or "1" are allowed
# NOTE: This pattern is duplicated in src/static/js/admin.js - keep in sync
SLUG_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


class RoomResponse(BaseModel):
    """Response model for a room."""

    id: int = Field(description="Room ID")
    listing_id: int = Field(description="Parent listing ID")
    cloudbeds_room_id: str = Field(description="Cloudbeds room ID")
    room_name: str = Field(description="Room name")
    room_type_name: str | None = Field(default=None, description="Room type name")
    ical_url_slug: str = Field(description="iCal URL slug")
    enabled: bool = Field(description="Whether room is enabled for iCal export")
    created_at: str | None = Field(default=None, description="Creation timestamp")
    updated_at: str | None = Field(default=None, description="Last update timestamp")


class RoomUpdateRequest(BaseModel):
    """Request model for updating a room."""

    enabled: bool | None = Field(default=None, description="Enable/disable room")
    ical_url_slug: str | None = Field(
        default=None,
        description="Custom iCal URL slug",
        max_length=100,
    )

    @field_validator("ical_url_slug")
    @classmethod
    def validate_slug_format(cls, v: str | None) -> str | None:
        """Validate slug contains only URL-safe characters."""
        if v is not None:
            if not SLUG_PATTERN.match(v):
                raise ValueError(
                    "Slug must start and end with a letter or number, "
                    "and contain only lowercase letters, numbers, and hyphens"
                )
            if "--" in v:
                raise ValueError("Slug cannot contain consecutive hyphens")
        return v


def _format_datetime(dt: Any) -> str | None:
    """Format datetime to ISO string with UTC timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return str(dt.isoformat())


def _room_to_response(room: Any) -> dict[str, Any]:
    """Convert room model to response dict."""
    return {
        "id": room.id,
        "listing_id": room.listing_id,
        "cloudbeds_room_id": room.cloudbeds_room_id,
        "room_name": room.room_name,
        "room_type_name": room.room_type_name,
        "ical_url_slug": room.ical_url_slug,
        "enabled": room.enabled,
        "created_at": _format_datetime(room.created_at),
        "updated_at": _format_datetime(room.updated_at),
    }


@router.get("/{room_id}", response_model=RoomResponse)
async def get_room(
    room_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get a specific room.

    Args:
        room_id: Room ID.
        db: Database session.

    Returns:
        Room details.

    Raises:
        HTTPException: 404 if room not found.
    """
    repo = RoomRepository(db)
    room = await repo.get_by_id(room_id)

    if not room:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found",
        )

    return _room_to_response(room)


@router.patch("/{room_id}", response_model=RoomResponse)
async def update_room(
    room_id: int,
    request: RoomUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Update a room configuration.

    Args:
        room_id: Room ID.
        request: Update data.
        db: Database session.

    Returns:
        Updated room.

    Raises:
        HTTPException: 404 if room not found.
        HTTPException: 400 if slug already in use.
    """
    repo = RoomRepository(db)
    room = await repo.get_by_id(room_id)

    if not room:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found",
        )

    # Update enabled status if provided
    if request.enabled is not None:
        room.enabled = request.enabled

    # Update slug if provided
    if request.ical_url_slug is not None:
        # Check for slug conflicts within the same listing
        existing_slugs = await repo.get_all_slugs_for_listing(room.listing_id)
        if (
            request.ical_url_slug in existing_slugs
            and request.ical_url_slug != room.ical_url_slug
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slug already in use by another room in this listing",
            )
        room.ical_url_slug = request.ical_url_slug

    try:
        await db.commit()
    except IntegrityError as err:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Slug already in use by another room (conflict during update)",
        ) from err
    await db.refresh(room)

    logger.info("Updated room %s (listing %s)", room.id, room.listing_id)

    return _room_to_response(room)
