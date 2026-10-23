# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Booking diagnostics API endpoints."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.providers.base import RECOGNISED_NON_EXPORT_STATUSES
from src.repositories.booking_repository import (
    BookingRepository,
    WithheldBookingRow,
)
from src.services.sync_service import KNOWN_BOOKING_STATUSES
from src.utils.stay_time import (
    get_timezone,
    has_meaningful_times,
    to_local_date,
    to_local_datetime,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bookings", tags=["Bookings"])

# Every status the system has a meaning for: the ones that drive the normal
# lifecycle plus the ones we understand but deliberately never export.
#
# Composed here, in the API layer, from the two existing constants.  The API
# may import from both ``services`` and ``providers``; the repository may
# not, so it takes this set as a parameter instead.
#
# Anything outside this set is a status we have no mapping for.  Those are
# the bookings worth an operator's attention: the raw string is the evidence
# that tells us which mapping to add.  Recognised-but-non-exporting statuses
# are excluded on purpose — they are normal on a healthy account, and
# surfacing them would make "Needs attention" permanently non-empty.
CLASSIFIED_STATUSES = KNOWN_BOOKING_STATUSES | RECOGNISED_NON_EXPORT_STATUSES


class WithheldBooking(BaseModel):
    """A booking withheld from the iCal export by an unknown status.

    Deliberately contains no guest PII: admin screenshots are routinely
    pasted into public issue reports.
    """

    listing_id: int = Field(description="Listing ID the booking belongs to")
    listing_name: str = Field(description="Listing name")
    listing_timezone: str = Field(description="IANA timezone of the listing")
    pms_booking_id: str = Field(description="PMS reservation ID")
    room_id: int | None = Field(default=None, description="Room ID, if assigned")
    all_day: bool = Field(
        description=(
            "True when the provider gave dates only.  All-day stays have no "
            "instant, so their dates must not be timezone-converted."
        )
    )
    check_in_date: str = Field(
        description=(
            "Check-in: a plain ``YYYY-MM-DD`` calendar date when ``all_day``, "
            "otherwise an ISO 8601 timestamp with the listing's UTC offset"
        )
    )
    check_out_date: str = Field(
        description=(
            "Check-out: a plain ``YYYY-MM-DD`` calendar date when "
            "``all_day``, otherwise an ISO 8601 timestamp with the listing's "
            "UTC offset"
        )
    )
    status: str = Field(
        description="Raw provider status, stored verbatim (may be empty)"
    )


class WithheldBookingsResponse(BaseModel):
    """Response model for withheld bookings."""

    bookings: list[WithheldBooking] = Field(description="Withheld bookings")
    count: int = Field(description="Number of withheld bookings")


def serialise_withheld(row: WithheldBookingRow) -> dict[str, Any]:
    """Serialise a withheld booking row for the admin UI.

    Dates are rendered unambiguously so the browser cannot reinterpret
    them.  An all-day stay has no instant, so it is rendered as a plain
    calendar date and must not be timezone-converted by the client; a timed
    stay is rendered in the listing's timezone with an explicit UTC offset.

    Args:
        row: Withheld booking projection from the repository.

    Returns:
        JSON-ready mapping, free of guest PII.
    """
    tz = get_timezone(row.listing_timezone)
    all_day = not has_meaningful_times(row.check_in_date, row.check_out_date)
    if all_day:
        check_in = to_local_date(row.check_in_date, tz).isoformat()
        check_out = to_local_date(row.check_out_date, tz).isoformat()
    else:
        check_in = to_local_datetime(row.check_in_date, tz).isoformat()
        check_out = to_local_datetime(row.check_out_date, tz).isoformat()

    return {
        "listing_id": row.listing_id,
        "listing_name": row.listing_name,
        "listing_timezone": row.listing_timezone,
        "pms_booking_id": row.pms_booking_id,
        "room_id": row.room_id,
        "all_day": all_day,
        "check_in_date": check_in,
        "check_out_date": check_out,
        "status": row.status,
    }


@router.get("/withheld", response_model=WithheldBookingsResponse)
async def get_withheld_bookings(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get current and future bookings withheld from the iCal export.

    Covers every enabled listing in a single query so the admin UI, which
    polls this, does not pay a query per listing.  Statuses are returned
    exactly as the provider reported them — that raw string is the
    diagnostic value.

    Args:
        db: Database session.

    Returns:
        Withheld bookings annotated with their listing, plus a count.
    """
    rows = await BookingRepository(db).get_withheld_for_enabled_listings(
        CLASSIFIED_STATUSES
    )
    return {
        "bookings": [serialise_withheld(row) for row in rows],
        "count": len(rows),
    }
