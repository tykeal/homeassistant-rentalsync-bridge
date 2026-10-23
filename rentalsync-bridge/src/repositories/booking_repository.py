# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Repository for Booking database operations."""

from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.booking import Booking
from src.models.listing import Listing


class BookingRepository:
    """Repository for Booking CRUD operations.

    Provides async database operations for Booking entities with
    proper query optimization for iCal generation.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_id(self, booking_id: int) -> Booking | None:
        """Get booking by ID.

        Args:
            booking_id: Booking primary key.

        Returns:
            Booking if found, None otherwise.
        """
        result = await self._session.execute(
            select(Booking).where(Booking.id == booking_id)
        )
        return result.scalar_one_or_none()

    async def get_by_pms_id(
        self, listing_id: int, pms_booking_id: str
    ) -> Booking | None:
        """Get booking by PMS booking ID and listing.

        Args:
            listing_id: Associated listing ID.
            pms_booking_id: PMS reservation ID.

        Returns:
            Booking if found, None otherwise.
        """
        result = await self._session.execute(
            select(Booking).where(
                Booking.listing_id == listing_id,
                Booking.pms_booking_id == pms_booking_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_listing(self, listing_id: int) -> Sequence[Booking]:
        """Get all bookings for a listing.

        Args:
            listing_id: Listing ID to filter by.

        Returns:
            Sequence of bookings for the listing.
        """
        result = await self._session.execute(
            select(Booking)
            .where(Booking.listing_id == listing_id)
            .order_by(Booking.check_in_date)
        )
        return result.scalars().all()

    async def get_confirmed_for_listing(
        self, listing_id: int, room_id: int | None = None
    ) -> Sequence[Booking]:
        """Get confirmed bookings for a listing (for iCal generation).

        Only returns bookings with status 'confirmed' and checkout in future
        or within last 7 days (to handle recently departed guests).

        Args:
            listing_id: Listing ID to filter by.
            room_id: Optional room ID to filter by. If None, returns all bookings.

        Returns:
            Sequence of confirmed bookings.
        """
        cutoff_date = datetime.now(UTC) - timedelta(days=7)

        # Build query with optional room filter
        query = select(Booking).where(
            Booking.listing_id == listing_id,
            Booking.status.in_(["confirmed", "checked_in", "checked_out"]),
            Booking.check_out_date >= cutoff_date,
        )

        # Add room filter if provided
        if room_id is not None:
            query = query.where(Booking.room_id == room_id)

        query = query.order_by(Booking.check_in_date)

        result = await self._session.execute(query)
        return result.scalars().all()

    async def get_withheld_for_listing(
        self, listing_id: int, classified_statuses: Collection[str]
    ) -> Sequence[Booking]:
        """Get bookings withheld from export due to an unclassified status.

        A booking whose stored status is outside ``classified_statuses`` is
        one the system has no mapping for, so the sync withheld it from the
        iCal export.  The empty string (a provider that reported no status
        at all) is an unclassified status and is therefore returned.

        Only current or future stays are returned; a withheld stay that has
        already ended is not actionable.  Unlike
        ``get_confirmed_for_listing`` there is deliberately no grace window.

        Args:
            listing_id: Listing ID to filter by.
            classified_statuses: Statuses the system understands.  Composed
                by the caller so this repository stays free of status
                vocabulary (and of a dependency on the service layer).

        Returns:
            Sequence of withheld bookings ordered by check-in date.
        """
        result = await self._session.execute(
            select(Booking)
            .where(
                Booking.listing_id == listing_id,
                Booking.status.notin_(list(classified_statuses)),
                Booking.check_out_date >= datetime.now(UTC),
            )
            .order_by(Booking.check_in_date)
        )
        return result.scalars().all()

    async def count_withheld_for_enabled_listings(
        self, classified_statuses: Collection[str]
    ) -> int:
        """Count withheld bookings across enabled listings.

        Bookings on disabled listings are never exported, so they are not
        actionable and are excluded.

        Args:
            classified_statuses: Statuses the system understands.

        Returns:
            Number of current or future bookings with an unclassified status.
        """
        result = await self._session.execute(
            select(func.count(Booking.id))
            .join(Listing, Listing.id == Booking.listing_id)
            .where(
                Listing.enabled.is_(True),
                Booking.status.notin_(list(classified_statuses)),
                Booking.check_out_date >= datetime.now(UTC),
            )
        )
        return result.scalar() or 0

    async def get_for_listing_in_range(
        self,
        listing_id: int,
        start_date: datetime,
        end_date: datetime,
    ) -> Sequence[Booking]:
        """Get bookings for listing within date range.

        Args:
            listing_id: Listing ID to filter by.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            Sequence of bookings overlapping with date range.
        """
        result = await self._session.execute(
            select(Booking)
            .where(
                Booking.listing_id == listing_id,
                Booking.check_in_date <= end_date,
                Booking.check_out_date >= start_date,
            )
            .order_by(Booking.check_in_date)
        )
        return result.scalars().all()

    async def create(self, booking: Booking) -> Booking:
        """Create a new booking.

        Args:
            booking: Booking entity to create.

        Returns:
            Created booking with ID.
        """
        self._session.add(booking)
        await self._session.flush()
        await self._session.refresh(booking)
        return booking

    async def update(self, booking: Booking) -> Booking:
        """Update an existing booking.

        Args:
            booking: Booking entity with updates.

        Returns:
            Updated booking.
        """
        await self._session.flush()
        await self._session.refresh(booking)
        return booking

    async def delete(self, booking: Booking) -> None:
        """Delete a booking.

        Args:
            booking: Booking entity to delete.
        """
        await self._session.delete(booking)
        await self._session.flush()

    async def mark_cancelled(self, booking: Booking) -> Booking:
        """Mark a booking as cancelled.

        Args:
            booking: Booking to mark as cancelled.

        Returns:
            Updated booking with cancelled status.
        """
        booking.status = "cancelled"
        return await self.update(booking)

    async def purge_old_bookings(self, days: int = 90) -> int:
        """Purge bookings older than specified days.

        Args:
            days: Number of days after checkout to keep bookings.

        Returns:
            Number of bookings deleted.
        """
        cutoff_date = datetime.now(UTC) - timedelta(days=days)
        result = cast(
            "CursorResult[tuple[()]]",
            await self._session.execute(
                delete(Booking).where(Booking.check_out_date < cutoff_date)
            ),
        )
        await self._session.flush()
        return result.rowcount or 0

    async def purge_cancelled_bookings(self, days: int = 30) -> int:
        """Purge cancelled bookings older than specified days.

        Args:
            days: Number of days to keep cancelled bookings.

        Returns:
            Number of bookings deleted.
        """
        cutoff_date = datetime.now(UTC) - timedelta(days=days)
        result = cast(
            "CursorResult[tuple[()]]",
            await self._session.execute(
                delete(Booking).where(
                    Booking.status == "cancelled",
                    Booking.updated_at < cutoff_date,
                )
            ),
        )
        await self._session.flush()
        return result.rowcount or 0

    async def upsert(self, booking: Booking) -> tuple[Booking, bool]:
        """Insert or update a booking.

        Args:
            booking: Booking entity to upsert.

        Returns:
            Tuple of (booking, was_created) where was_created is True for new bookings.
        """
        existing = await self.get_by_pms_id(booking.listing_id, booking.pms_booking_id)

        if existing is None:
            created = await self.create(booking)
            return (created, True)

        # Update existing booking
        existing.room_id = booking.room_id
        existing.guest_name = booking.guest_name
        existing.guest_phone_last4 = booking.guest_phone_last4
        existing.check_in_date = booking.check_in_date
        existing.check_out_date = booking.check_out_date
        existing.status = booking.status
        existing.custom_data = booking.custom_data
        existing.pms_updated_at = booking.pms_updated_at
        existing.enrichment_data = booking.enrichment_data
        existing.last_fetched_at = datetime.now(UTC)

        updated = await self.update(existing)
        return (updated, False)
