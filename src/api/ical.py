# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""iCal feed API endpoint for room-level calendars."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.repositories.booking_repository import BookingRepository
from src.repositories.custom_field_repository import CustomFieldRepository
from src.repositories.listing_repository import ListingRepository
from src.repositories.room_repository import RoomRepository
from src.services.calendar_service import CalendarCache, CalendarService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["iCal"])

# Shared cache instance
_calendar_cache = CalendarCache()


def get_calendar_service() -> CalendarService:
    """Get calendar service with shared cache.

    Returns:
        CalendarService instance with cache.
    """
    return CalendarService(cache=_calendar_cache)


@router.get(
    "/ical/{listing_slug}/{room_slug}.ics",
    response_class=Response,
    responses={
        200: {
            "content": {"text/calendar": {}},
            "description": "iCal calendar feed for a specific room",
        },
        404: {"description": "Room not found or disabled"},
    },
)
async def get_room_ical_feed(
    listing_slug: str,
    room_slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    calendar_service: Annotated[CalendarService, Depends(get_calendar_service)],
) -> Response:
    """Get iCal feed for a specific room in a listing.

    Args:
        listing_slug: Listing URL slug.
        room_slug: Room URL slug.
        db: Database session.
        calendar_service: Calendar service for iCal generation.

    Returns:
        iCal calendar as text/calendar response.

    Raises:
        HTTPException: 404 if room not found, listing not found,
                       or either is disabled.
    """
    room_repo = RoomRepository(db)
    booking_repo = BookingRepository(db)
    custom_field_repo = CustomFieldRepository(db)

    # Get room by listing and room slugs
    room = await room_repo.get_by_slug(listing_slug, room_slug)

    if not room:
        logger.warning("iCal request for unknown room: %s/%s", listing_slug, room_slug)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found",
        )

    if not room.enabled:
        logger.warning("iCal request for disabled room: %s/%s", listing_slug, room_slug)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found",
        )

    # Check if listing is enabled
    listing_repo = ListingRepository(db)
    listing = await listing_repo.get_by_id(room.listing_id)

    if not listing or not listing.enabled:
        logger.warning(
            "iCal request for room in disabled listing: %s/%s",
            listing_slug,
            room_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Room not found",
        )

    # Fetch confirmed bookings for this specific room
    bookings = await booking_repo.get_confirmed_for_listing(listing.id, room_id=room.id)
    custom_fields = await custom_field_repo.get_enabled_for_listing(listing.id)

    # Generate iCal with room-specific configuration
    ical_content = calendar_service.generate_ical(
        listing=listing,
        bookings=list(bookings),
        custom_fields=list(custom_fields),
        room_slug=room_slug,
    )

    return Response(
        content=ical_content,
        media_type="text/calendar",
        headers={
            "Content-Disposition": f'attachment; filename="{room_slug}.ics"',
        },
    )


@router.get(
    "/ical/{slug}.ics",
    responses={
        410: {"description": "Endpoint format has changed"},
    },
)
async def get_legacy_ical_feed(slug: str) -> Response:
    """Legacy endpoint - return helpful error about new URL format.

    The iCal endpoint format changed from property-level to room-level.
    Old: /ical/{listing-slug}.ics
    New: /ical/{listing-slug}/{room-slug}.ics

    Args:
        slug: The old-style listing slug.

    Raises:
        HTTPException: 410 Gone with migration instructions.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            f"iCal URL format has changed. Property-level calendars are no longer "
            f"supported. Use room-level URLs: /ical/{slug}/{{room-slug}}.ics - "
            f"Check the admin UI for room-specific calendar URLs."
        ),
    )
