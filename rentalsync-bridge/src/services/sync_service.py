# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sync service for synchronizing bookings from Cloudbeds."""

import logging
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database import get_session_factory
from src.models.booking import Booking
from src.models.listing import Listing
from src.models.oauth_credential import OAuthCredential
from src.repositories.available_field_repository import AvailableFieldRepository
from src.repositories.booking_repository import BookingRepository
from src.repositories.room_repository import RoomRepository
from src.services.calendar_service import CalendarCache
from src.services.cloudbeds_service import CloudbedsService, CloudbedsServiceError

logger = logging.getLogger(__name__)


class SyncServiceError(Exception):
    """Exception raised for sync service errors."""

    pass


class SyncService:
    """Service for synchronizing bookings from Cloudbeds.

    Handles periodic fetching of reservations from Cloudbeds API and
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
        credential: OAuthCredential,
    ) -> dict[str, int]:
        """Sync bookings for a single listing.

        Args:
            listing: Listing to sync bookings for.
            credential: OAuth credential for API access.

        Returns:
            Dict with counts: inserted, updated, cancelled.

        Raises:
            SyncServiceError: If sync fails.
        """
        if not listing.sync_enabled:
            logger.debug("Skipping disabled sync for listing %s", listing.cloudbeds_id)
            return {"inserted": 0, "updated": 0, "cancelled": 0}

        try:
            # Initialize Cloudbeds service with tokens or API key
            cloudbeds = CloudbedsService(
                access_token=credential.access_token,
                refresh_token=credential.refresh_token,
                api_key=credential.api_key,
            )

            # Fetch reservations from Cloudbeds
            reservations = await cloudbeds.get_reservations(listing.cloudbeds_id)

            counts = await self._process_reservations(listing, reservations)

            # Update sync status on success
            listing.last_sync_at = datetime.now(UTC)
            listing.last_sync_error = None

            return counts

        except CloudbedsServiceError as e:
            # Update sync error status using a separate session to avoid
            # affecting any pending changes in the caller's transaction
            error_msg = str(e)
            await self._persist_sync_error(listing.id, error_msg)
            logger.error(
                "Sync failed for listing %s: %s",
                listing.cloudbeds_id,
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
        reservations: list[dict],
    ) -> dict[str, int]:
        """Process reservations and update local bookings.

        Args:
            listing: Listing being synced.
            reservations: List of reservation dicts from Cloudbeds.

        Returns:
            Dict with counts: inserted, updated, cancelled.
        """
        counts = {"inserted": 0, "updated": 0, "cancelled": 0}
        seen_booking_ids: set[str] = set()
        seen_reservation_ids: set[str] = set()  # Track base reservation IDs
        fields_discovered = False

        for reservation in reservations:
            cloudbeds_booking_id = reservation.get("id") or reservation.get(
                "reservationID"
            )
            if not cloudbeds_booking_id:
                logger.warning("Skipping reservation with no ID")
                continue

            # Track the reservation ID from API for cancellation detection
            seen_reservation_ids.add(str(cloudbeds_booking_id))

            # Discover available fields from first reservation only (performance)
            if not fields_discovered:
                await self._available_field_repo.discover_fields_from_reservation(
                    listing.id, reservation
                )
                fields_discovered = True

            # Extract booking data
            booking_data = self._extract_booking_data(reservation)

            # Skip reservations with invalid dates (T088)
            if not booking_data["check_in_date"] or not booking_data["check_out_date"]:
                logger.warning(
                    "Skipping reservation %s with invalid dates", cloudbeds_booking_id
                )
                continue

            # Create bookings for this reservation
            inserted, updated, new_ids = await self._create_bookings_for_reservation(
                listing, cloudbeds_booking_id, booking_data
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

            booking_id = existing_booking.cloudbeds_booking_id
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
            listing.cloudbeds_id,
            counts["inserted"],
            counts["updated"],
            counts["cancelled"],
        )

        return counts

    async def _create_bookings_for_reservation(
        self,
        listing: Listing,
        cloudbeds_booking_id: str,
        booking_data: dict,
    ) -> tuple[int, int, set[str]]:
        """Create booking records for a reservation.

        For multi-room reservations, creates one booking per room.

        Args:
            listing: The listing being synced.
            cloudbeds_booking_id: The Cloudbeds reservation ID.
            booking_data: Extracted booking data from _extract_booking_data.

        Returns:
            Tuple of (inserted_count, updated_count, set of booking IDs created).
        """
        inserted = 0
        updated = 0
        booking_ids: set[str] = set()

        cloudbeds_room_ids = booking_data.get("cloudbeds_room_ids", [])
        rooms_data = booking_data.get("rooms_data", [])
        base_custom_data = booking_data.get("base_custom_data", {})

        # Build a lookup from room ID to room data for merging
        room_data_by_id = self._build_room_data_lookup(rooms_data)

        # If no rooms specified, create booking without room association
        if not cloudbeds_room_ids:
            booking_id = str(cloudbeds_booking_id)
            booking_ids.add(booking_id)
            # Use base custom data as-is (no room-specific data to merge)
            final_booking_data = {
                **booking_data,
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
            for cloudbeds_room_id in cloudbeds_room_ids:
                # Always use composite booking ID when room ID is present
                # This ensures consistent ID format regardless of room count changes
                # Use "::" delimiter to avoid ambiguity with IDs containing hyphens
                booking_id = f"{cloudbeds_booking_id}::{cloudbeds_room_id}"
                booking_ids.add(booking_id)

                room = await self._room_repo.get_by_cloudbeds_id(
                    listing.id, cloudbeds_room_id
                )
                db_room_id: int | None = room.id if room else None
                if not room:
                    logger.warning(
                        "Room %s not found for booking %s - booking will not "
                        "appear in room calendars. Sync rooms first.",
                        cloudbeds_room_id,
                        cloudbeds_booking_id,
                    )

                # Merge room-specific data into custom_data
                room_specific_data = room_data_by_id.get(cloudbeds_room_id)
                custom_data = self._merge_room_custom_data(
                    base_custom_data, room_specific_data
                )
                final_booking_data = {
                    **booking_data,
                    "custom_data": custom_data if custom_data else None,
                }

                was_created = await self._upsert_single_booking(
                    listing, booking_id, db_room_id, final_booking_data
                )
                if was_created:
                    inserted += 1
                else:
                    updated += 1

        return inserted, updated, booking_ids

    @staticmethod
    def _build_room_data_lookup(rooms_data: list) -> dict[str, dict]:
        """Build a lookup dict from room ID to room data.

        Args:
            rooms_data: List of room dicts from Cloudbeds API.

        Returns:
            Dict mapping room ID strings to their room data dicts.
        """
        room_data_by_id: dict[str, dict] = {}
        if rooms_data and isinstance(rooms_data, list):
            for room in rooms_data:
                if isinstance(room, dict):
                    cb_room_id = room.get("roomID") or room.get("roomId")
                    if cb_room_id:
                        room_data_by_id[str(cb_room_id)] = room
        return room_data_by_id

    @staticmethod
    def _merge_room_custom_data(
        base_custom_data: dict,
        room_data: dict | None,
    ) -> dict:
        """Merge room-specific data into base custom data.

        Args:
            base_custom_data: Custom data from reservation level.
            room_data: Room-specific data dict from rooms array.

        Returns:
            Merged custom data with room-specific values taking precedence.
        """
        merged = base_custom_data.copy()

        if room_data and isinstance(room_data, dict):
            for key, value in room_data.items():
                # Skip None, empty, and complex values
                if value is None or value == "" or isinstance(value, (dict, list)):
                    continue
                # Skip ID fields
                if key.lower().endswith("id") or key == "id":
                    continue
                # Room data overrides reservation-level data
                merged[key] = str(value)

        return merged

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
            booking_id: The unique booking ID (may be composite for multi-room).
            room_id: The room ID (None if no room association).
            booking_data: Extracted booking data.

        Returns:
            True if booking was created, False if updated.
        """
        booking = Booking(
            listing_id=listing.id,
            room_id=room_id,
            cloudbeds_booking_id=booking_id,
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
        """Extract the base Cloudbeds reservation ID from a booking ID.

        For multi-room bookings, the ID format is "{reservationID}::{roomID}".
        This extracts just the reservation ID portion.

        Args:
            booking_id: The booking ID (may be composite).

        Returns:
            The base reservation ID.
        """
        # Multi-room booking IDs use "::" delimiter to separate reservation and room
        # rsplit with maxsplit=1 splits at the last "::", returning original if none
        return booking_id.rsplit("::", 1)[0]

    def _extract_booking_data(self, reservation: dict) -> dict:
        """Extract booking data from Cloudbeds reservation.

        Args:
            reservation: Reservation dict from Cloudbeds API.

        Returns:
            Dict with booking fields for database.
        """
        # Extract guest name
        guest_name = reservation.get("guestName")
        if not guest_name:
            first = reservation.get("guestFirstName", "")
            last = reservation.get("guestLastName", "")
            guest_name = f"{first} {last}".strip() or None

        # Extract phone from guestList (requires includeGuestsDetails=true in API call)
        # guestList is a dict keyed by guestID, not a list
        phone = None
        guest_list = reservation.get("guestList", {})
        if guest_list and isinstance(guest_list, dict):
            # Get primary guest by guestID from reservation, or first guest
            primary_guest_id = reservation.get("guestID")
            if primary_guest_id and primary_guest_id in guest_list:
                guest = guest_list[primary_guest_id]
            else:
                # Fallback to first guest in the list
                guest = next(iter(guest_list.values()), {})

            if isinstance(guest, dict):
                # Prefer mobile (guestCellPhone), fallback to generic (guestPhone)
                phone = guest.get("guestCellPhone") or guest.get("guestPhone")

        phone_last4 = CloudbedsService.extract_phone_last4(phone)

        # Parse dates
        check_in = self._parse_date(reservation.get("startDate"))
        check_out = self._parse_date(reservation.get("endDate"))

        # Extract status
        status = reservation.get("status", "confirmed").lower()
        if status not in ("confirmed", "checked_in", "checked_out", "cancelled"):
            status = "confirmed"

        # Extract room IDs and rooms data
        cloudbeds_room_ids = self._extract_room_ids(reservation)
        rooms_data = reservation.get("rooms", [])

        # Build base custom data from reservation (without room-specific data)
        # Room-specific data will be merged in _create_bookings_for_reservation
        base_custom_data = self._extract_custom_data(reservation, phone_last4)

        # Note: phone_last4 is stored in two places for different use cases:
        # - guest_phone_last4: direct booking attribute for legacy/default iCal display
        # - custom_data["guest_phone_last4"]: for configurable custom field output
        #   (added in _extract_custom_data method)
        return {
            "guest_name": guest_name,
            "guest_phone_last4": phone_last4,
            "check_in_date": check_in,
            "check_out_date": check_out,
            "status": status,
            "cloudbeds_room_ids": cloudbeds_room_ids,
            "rooms_data": rooms_data,  # Keep rooms for per-booking custom data
            "base_custom_data": base_custom_data if base_custom_data else {},
        }

    @staticmethod
    def _extract_room_ids(reservation: dict) -> list[str]:
        """Extract room IDs from Cloudbeds reservation.

        Prefers nested rooms array (authoritative), falls back to top-level roomID.

        Args:
            reservation: Reservation dict from Cloudbeds API.

        Returns:
            List of room ID strings.
        """
        cloudbeds_room_ids: list[str] = []
        seen_room_ids: set[str] = set()
        rooms = reservation.get("rooms", [])

        if rooms and isinstance(rooms, list):
            for room in rooms:
                if isinstance(room, dict):
                    room_id = room.get("roomID") or room.get("roomId")
                    if room_id:
                        room_id_str = str(room_id)
                        if room_id_str not in seen_room_ids:
                            seen_room_ids.add(room_id_str)
                            cloudbeds_room_ids.append(room_id_str)

        if not cloudbeds_room_ids:
            top_level_room_id = reservation.get("roomID") or reservation.get("roomId")
            if top_level_room_id:
                cloudbeds_room_ids.append(str(top_level_room_id))

        return cloudbeds_room_ids

    @staticmethod
    def _extract_custom_data(
        reservation: dict,
        phone_last4: str | None,
        room_data: dict | None = None,
    ) -> dict:
        """Extract custom field data from Cloudbeds reservation.

        Dynamically extracts all scalar (non-dict, non-list) values from
        the reservation and optionally from room-specific data, making
        them available for custom field configuration.

        Args:
            reservation: Reservation dict from Cloudbeds API.
            phone_last4: Extracted phone last 4 digits.
            room_data: Optional room-specific data dict from rooms array.

        Returns:
            Dict of custom field values keyed by original Cloudbeds field name.
        """
        custom_data: dict[str, str] = {}

        for key, value in reservation.items():
            # Skip None, empty, and complex values (dicts, lists)
            if value is None or value == "" or isinstance(value, (dict, list)):
                continue
            # Skip ID fields (internal use only)
            if key.lower().endswith("id") or key == "id":
                continue
            # Store the value as string
            custom_data[key] = str(value)

        # Merge room-specific data (e.g., roomTypeName, roomName)
        if room_data and isinstance(room_data, dict):
            for key, value in room_data.items():
                # Skip None, empty, and complex values
                if value is None or value == "" or isinstance(value, (dict, list)):
                    continue
                # Skip ID fields
                if key.lower().endswith("id") or key == "id":
                    continue
                # Room data overrides top-level reservation data
                custom_data[key] = str(value)

        # Add guest_phone_last4 as a special computed field
        if phone_last4:
            custom_data["guest_phone_last4"] = phone_last4

        return custom_data

    @staticmethod
    def _parse_date(date_str: str | None) -> datetime | None:
        """Parse date string to datetime.

        Args:
            date_str: Date string in various formats.

        Returns:
            Datetime object or None.
        """
        if not date_str:
            return None

        # Try common formats
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(date_str, fmt)
                # Assume UTC if no timezone
                return dt.replace(tzinfo=UTC)
            except ValueError:
                continue

        logger.warning("Could not parse date: %s", date_str)
        return None
