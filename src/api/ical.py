# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""iCal feed API endpoint."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.repositories.booking_repository import BookingRepository
from src.repositories.custom_field_repository import CustomFieldRepository
from src.repositories.listing_repository import ListingRepository
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
    "/ical/{slug}.ics",
    response_class=Response,
    responses={
        200: {
            "content": {"text/calendar": {}},
            "description": "iCal calendar feed",
        },
        404: {"description": "Listing not found"},
    },
)
async def get_ical_feed(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    calendar_service: Annotated[CalendarService, Depends(get_calendar_service)],
) -> Response:
    """Get iCal feed for a listing.

    Args:
        slug: Listing URL slug.
        db: Database session.
        calendar_service: Calendar service for iCal generation.

    Returns:
        iCal calendar as text/calendar response.

    Raises:
        HTTPException: 404 if listing not found or not enabled.
    """
    listing_repo = ListingRepository(db)
    booking_repo = BookingRepository(db)
    custom_field_repo = CustomFieldRepository(db)

    listing = await listing_repo.get_by_slug(slug)

    if not listing:
        logger.warning("iCal request for unknown slug: %s", slug)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )

    if not listing.enabled:
        logger.warning("iCal request for disabled listing: %s", slug)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found",
        )

    # Fetch bookings and custom fields
    bookings = await booking_repo.get_for_listing(listing.id)
    custom_fields = await custom_field_repo.get_for_listing(listing.id)

    # Filter only enabled custom fields
    enabled_fields = [cf for cf in custom_fields if cf.enabled]

    # Generate iCal
    ical_content = calendar_service.generate_ical(listing, bookings, enabled_fields)

    return Response(
        content=ical_content,
        media_type="text/calendar",
        headers={
            "Content-Disposition": f'attachment; filename="{slug}.ics"',
        },
    )
