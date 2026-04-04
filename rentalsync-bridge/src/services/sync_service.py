# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sync service for synchronizing bookings via PMS providers."""

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database import get_session_factory
from src.models.booking import Booking
from src.models.listing import Listing
from src.providers.base import (
    PMSGuest,
    PMSProvider,
    PMSProviderError,
    PMSReservation,
)
from src.repositories.available_field_repository import (
    AvailableFieldRepository,
    should_exclude_field,
)
from src.repositories.booking_repository import BookingRepository
from src.repositories.room_repository import RoomRepository
from src.services.calendar_service import CalendarCache

logger = logging.getLogger(__name__)

PHONE_LAST_DIGITS = 4


class SyncServiceError(Exception):
    """Exception raised for sync service errors."""

    pass


class SyncService:
    """Service for synchronizing bookings from any PMS provider.

    Handles periodic fetching of reservations via PMSProvider and
    updating local booking cache with INSERT/UPDATE/cancelled logic.
    """

    def __init__(
        self,
        session: AsyncSession,
        calendar_cache: CalendarCache | None = None,
        session_factory: "async_sessionmaker[AsyncSession] | None" = None,
    ) -> None:
        """Initialize SyncService.

        Args:
            session: Async database session.
            calendar_cache: Optional calendar cache to invalidate on sync.
            session_factory: Optional session factory for error persistence.
                           If not provided, uses the global factory.
        """
        self._session = session
        self._calendar_cache = calendar_cache
        self._session_factory = session_factory
        self._booking_repo = BookingRepository(session)
        self._room_repo = RoomRepository(session)
        self._available_field_repo = AvailableFieldRepository(session)

    async def sync_listing(
        self,
        listing: Listing,
        provider: PMSProvider,
    ) -> dict[str, int]:
        """Sync bookings for a single listing.

        Args:
            listing: Listing to sync bookings for.
            provider: PMSProvider instance for API access.

        Returns:
            Dict with counts: inserted, updated, cancelled.

        Raises:
            SyncServiceError: If sync fails.
        """
        if not listing.sync_enabled:
            logger.debug("Skipping disabled sync for listing %s", listing.pms_id)
            return {"inserted": 0, "updated": 0, "cancelled": 0}

        try:
            reservations = await provider.get_reservations(listing.pms_id)

            # Resolve guest names for reservations that lack them
            reservations = await self._resolve_guest_names(provider, reservations)

            # Enrich with provider custom fields (e.g. Guesty v3)
            reservations = await self._enrich_custom_fields(provider, reservations)

            counts = await self._process_reservations(listing, reservations)

            # Update sync status on success
            listing.last_sync_at = datetime.now(UTC)
            listing.last_sync_error = None

            return counts

        except PMSProviderError as e:
            # Update sync error status using a separate session to avoid
            # affecting any pending changes in the caller's transaction
            error_msg = str(e)
            await self._persist_sync_error(listing.id, error_msg)
            logger.error(
                "Sync failed for listing %s: %s",
                listing.pms_id,
                error_msg,
            )
            raise SyncServiceError(error_msg) from e

    async def _persist_sync_error(self, listing_id: int, error_msg: str) -> None:
        """Persist sync error status using a separate session.

        Uses a dedicated session to ensure error status is persisted
        even if the main session is rolled back.

        Args:
            listing_id: ID of the listing to update.
            error_msg: Error message to store.
        """
        factory = self._session_factory or get_session_factory()
        async with factory() as session:
            await session.execute(
                update(Listing)
                .where(Listing.id == listing_id)
                .values(last_sync_error=error_msg, last_sync_at=datetime.now(UTC))
            )
            await session.commit()

    async def _process_reservations(
        self,
        listing: Listing,
        reservations: list[PMSReservation],
    ) -> dict[str, int]:
        """Process reservations and update local bookings.

        Args:
            listing: Listing being synced.
            reservations: List of PMSReservation DTOs from provider.

        Returns:
            Dict with counts: inserted, updated, cancelled.
        """
        counts = {"inserted": 0, "updated": 0, "cancelled": 0}
        seen_booking_ids: set[str] = set()
        seen_reservation_ids: set[str] = set()

        # Batch discover fields from reservation custom_data
        raw_dicts = [r.custom_data for r in reservations if r.custom_data]
        if raw_dicts:
            await self._available_field_repo.discover_fields_from_reservations(
                listing.id, raw_dicts
            )

        for reservation in reservations:
            if not reservation.pms_booking_id:
                logger.warning("Skipping reservation with no ID")
                continue

            seen_reservation_ids.add(reservation.pms_booking_id)

            booking_data = self._extract_booking_data_from_dto(
                reservation,
            )

            if not booking_data["check_in_date"] or not booking_data["check_out_date"]:
                logger.warning(
                    "Skipping reservation %s with invalid dates",
                    reservation.pms_booking_id,
                )
                continue

            inserted, updated, new_ids = await self._create_bookings_for_reservation(
                listing,
                reservation.pms_booking_id,
                booking_data,
            )
            counts["inserted"] += inserted
            counts["updated"] += updated
            seen_booking_ids.update(new_ids)

        # Mark cancelled bookings (not in current fetch)
        # A booking is cancelled if neither its exact ID nor its base reservation ID
        # is in the seen sets. This handles transitions between single/multi-room.
        existing_bookings = await self._booking_repo.get_for_listing(listing.id)
        for existing_booking in existing_bookings:
            if existing_booking.status == "cancelled":
                continue

            booking_id = existing_booking.pms_booking_id
            # Extract base reservation ID (before any ::roomID suffix)
            base_reservation_id = self._extract_base_reservation_id(booking_id)

            # Keep if exact ID is seen OR if base reservation still exists
            if booking_id in seen_booking_ids:
                continue
            if base_reservation_id in seen_reservation_ids:
                # Room config changed - cancel old booking
                await self._booking_repo.mark_cancelled(existing_booking)
                counts["cancelled"] += 1
                continue
            # Reservation no longer exists at all
            await self._booking_repo.mark_cancelled(existing_booking)
            counts["cancelled"] += 1

        await self._session.commit()

        # Invalidate calendar cache AFTER commit to avoid race conditions
        # Use prefix invalidation to clear all room-level caches for this listing
        if self._calendar_cache and sum(counts.values()) > 0:
            self._calendar_cache.invalidate_prefix(listing.ical_url_slug)
            logger.debug("Invalidated cache for listing %s", listing.ical_url_slug)

        logger.info(
            "Synced listing %s: %d inserted, %d updated, %d cancelled",
            listing.pms_id,
            counts["inserted"],
            counts["updated"],
            counts["cancelled"],
        )

        return counts

    async def _create_bookings_for_reservation(
        self,
        listing: Listing,
        pms_booking_id: str,
        booking_data: dict,
    ) -> tuple[int, int, set[str]]:
        """Create booking records for a reservation.

        For multi-room reservations, creates one booking per room.

        Args:
            listing: The listing being synced.
            pms_booking_id: The PMS reservation ID.
            booking_data: Extracted booking data.

        Returns:
            Tuple of (inserted_count, updated_count, set of IDs).
        """
        inserted = 0
        updated = 0
        booking_ids: set[str] = set()

        # Extract transient keys that are only used locally, not passed to upsert
        room_ids = booking_data.get("room_ids", [])
        base_custom_data = booking_data.get("base_custom_data", {})

        # Build the base booking dict with only ORM-expected fields
        base_booking = {
            "guest_name": booking_data["guest_name"],
            "guest_phone_last4": booking_data["guest_phone_last4"],
            "check_in_date": booking_data["check_in_date"],
            "check_out_date": booking_data["check_out_date"],
            "status": booking_data["status"],
        }

        # If no rooms specified, create booking without room association
        if not room_ids:
            booking_id = str(pms_booking_id)
            booking_ids.add(booking_id)
            # Use base custom data as-is (no room-specific data to merge)
            final_booking_data = {
                **base_booking,
                "custom_data": base_custom_data if base_custom_data else None,
            }
            was_created = await self._upsert_single_booking(
                listing, booking_id, None, final_booking_data
            )
            if was_created:
                inserted += 1
            else:
                updated += 1
        else:
            # Create a booking for EACH room in the reservation
            for pms_room_id in room_ids:
                # Always use composite booking ID when room ID is present
                # Use "::" delimiter to avoid ambiguity with IDs
                booking_id = f"{pms_booking_id}::{pms_room_id}"
                booking_ids.add(booking_id)

                room = await self._room_repo.get_by_pms_id(listing.id, pms_room_id)
                db_room_id: int | None = room.id if room else None
                if not room:
                    logger.warning(
                        "Room %s not found for booking %s - booking will "
                        "not appear in room calendars. Sync rooms first.",
                        pms_room_id,
                        pms_booking_id,
                    )

                final_booking_data = {
                    **base_booking,
                    "custom_data": (base_custom_data if base_custom_data else None),
                }

                was_created = await self._upsert_single_booking(
                    listing, booking_id, db_room_id, final_booking_data
                )
                if was_created:
                    inserted += 1
                else:
                    updated += 1

        return inserted, updated, booking_ids

    async def _upsert_single_booking(
        self,
        listing: Listing,
        booking_id: str,
        room_id: int | None,
        booking_data: dict,
    ) -> bool:
        """Create or update a single booking record.

        Args:
            listing: The listing for this booking.
            booking_id: The unique booking ID (may be composite).
            room_id: The room ID (None if no room association).
            booking_data: Extracted booking data.

        Returns:
            True if booking was created, False if updated.
        """
        booking = Booking(
            listing_id=listing.id,
            room_id=room_id,
            pms_booking_id=booking_id,
            guest_name=booking_data["guest_name"],
            guest_phone_last4=booking_data["guest_phone_last4"],
            check_in_date=booking_data["check_in_date"],
            check_out_date=booking_data["check_out_date"],
            status=booking_data["status"],
            custom_data=booking_data["custom_data"],
        )
        _, was_created = await self._booking_repo.upsert(booking)
        return was_created

    @staticmethod
    def _extract_base_reservation_id(booking_id: str) -> str:
        """Extract the base reservation ID from a booking ID.

        For multi-room bookings, the ID format is
        ``{reservationID}::{roomID}``.

        Args:
            booking_id: The booking ID (may be composite).

        Returns:
            The base reservation ID.
        """
        return booking_id.rsplit("::", 1)[0]

    @staticmethod
    def _extract_booking_data_from_dto(
        reservation: PMSReservation,
    ) -> dict:
        """Extract booking data from a PMSReservation DTO.

        Args:
            reservation: Normalized reservation DTO from provider.

        Returns:
            Dict with booking fields for database.
        """
        status = reservation.status or "confirmed"
        if status not in (
            "confirmed",
            "checked_in",
            "checked_out",
            "cancelled",
        ):
            status = "confirmed"

        # Build base custom data — include phone_last4 if available
        base_custom_data: dict = {}
        if reservation.custom_data:
            for key, value in reservation.custom_data.items():
                if value is None or value == "":
                    continue
                if isinstance(value, (dict, list)):
                    continue
                if should_exclude_field(key):
                    continue
                base_custom_data[key] = str(value)

        phone_last4 = base_custom_data.get("guest_phone_last4")

        return {
            "guest_name": reservation.guest_name,
            "guest_phone_last4": phone_last4,
            "check_in_date": reservation.check_in,
            "check_out_date": reservation.check_out,
            "status": status,
            "room_ids": list(reservation.room_ids),
            "base_custom_data": base_custom_data,
        }

    @staticmethod
    def extract_phone_last4(phone: str | None) -> str | None:
        """Extract last 4 digits from a phone number.

        Args:
            phone: Full phone number string.

        Returns:
            Last 4 digits of phone number, or None.
        """
        if not phone:
            return None
        digits = re.sub(r"\D", "", phone)
        if len(digits) >= PHONE_LAST_DIGITS:
            return digits[-PHONE_LAST_DIGITS:]
        return None

    async def _resolve_guest_names(
        self,
        provider: PMSProvider,
        reservations: list[PMSReservation],
    ) -> list[PMSReservation]:
        """Resolve missing guest names via provider.get_guest().

        Batches unique guest_ids to avoid redundant API calls.
        Also adds ``guest_phone_last4``, ``guest_phone``, and
        ``guest_email`` to ``custom_data`` when available.

        Args:
            provider: Active PMS provider instance.
            reservations: Reservations that may lack guest_name.

        Returns:
            Updated list of PMSReservation DTOs.
        """
        # Collect unique guest_ids that need resolution
        ids_to_resolve: set[str] = set()
        for r in reservations:
            if r.guest_name is None and r.guest_id is not None:
                ids_to_resolve.add(r.guest_id)

        if not ids_to_resolve:
            return reservations

        # Batch resolve guests
        guest_cache: dict[str, PMSGuest | None] = {}
        for gid in ids_to_resolve:
            try:
                guest_cache[gid] = await provider.get_guest(gid)
            except PMSProviderError:
                logger.warning("Failed to resolve guest %s", gid)
                guest_cache[gid] = None

        # Rebuild reservations with resolved names / phone
        updated: list[PMSReservation] = []
        for r in reservations:
            resolved = r
            if r.guest_name is None and r.guest_id is not None:
                guest = guest_cache.get(r.guest_id)
                if guest is not None:
                    phone_last4 = self.extract_phone_last4(
                        guest.phone,
                    )
                    new_custom = dict(r.custom_data)
                    if phone_last4:
                        new_custom["guest_phone_last4"] = phone_last4
                    if guest.phone:
                        new_custom["guest_phone"] = guest.phone
                    if guest.email:
                        new_custom["guest_email"] = guest.email
                    resolved = PMSReservation(
                        pms_booking_id=r.pms_booking_id,
                        listing_pms_id=r.listing_pms_id,
                        guest_name=guest.full_name,
                        guest_id=r.guest_id,
                        check_in=r.check_in,
                        check_out=r.check_out,
                        status=r.status,
                        room_ids=r.room_ids,
                        custom_data=new_custom,
                    )
            updated.append(resolved)
        return updated

    async def _enrich_custom_fields(
        self,
        provider: PMSProvider,
        reservations: list[PMSReservation],
    ) -> list[PMSReservation]:
        """Fetch and merge provider custom fields into reservations.

        Only invoked when the provider exposes custom fields via a
        dedicated endpoint (``has_separate_custom_fields`` is True).
        Providers that embed custom fields directly in reservation
        payloads (e.g. Cloudbeds) are skipped entirely.

        Args:
            provider: Active PMS provider instance.
            reservations: Reservations to enrich.

        Returns:
            Updated list of PMSReservation DTOs.
        """
        if not provider.has_separate_custom_fields:
            return reservations

        # TODO: add bounded concurrency (asyncio.gather + semaphore)
        # for listings with many reservations.
        enriched: list[PMSReservation] = []
        for r in reservations:
            if not r.pms_booking_id:
                enriched.append(r)
                continue

            try:
                extra = await provider.get_custom_fields(
                    r.pms_booking_id,
                )
            except PMSProviderError:
                logger.warning(
                    "Failed to fetch custom fields for %s",
                    r.pms_booking_id,
                    exc_info=True,
                )
                extra = {}

            if extra:
                merged = {**r.custom_data, **extra}
                enriched.append(
                    PMSReservation(
                        pms_booking_id=r.pms_booking_id,
                        listing_pms_id=r.listing_pms_id,
                        guest_name=r.guest_name,
                        guest_id=r.guest_id,
                        check_in=r.check_in,
                        check_out=r.check_out,
                        status=r.status,
                        room_ids=r.room_ids,
                        custom_data=merged,
                    )
                )
            else:
                enriched.append(r)
        return enriched
