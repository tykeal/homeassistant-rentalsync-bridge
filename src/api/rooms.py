# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Room management API endpoints."""

import logging
from datetime import UTC
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.repositories.room_repository import RoomRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rooms", tags=["Rooms"])


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
    ical_url_slug: str | None = Field(default=None, description="Custom iCal URL slug")


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

    await db.commit()
    await db.refresh(room)

    logger.info("Updated room %s (listing %s)", room.id, room.listing_id)

    return _room_to_response(room)
