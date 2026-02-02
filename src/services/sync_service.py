# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Sync service for synchronizing bookings from Cloudbeds."""

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.booking import Booking
from src.models.listing import Listing
from src.models.oauth_credential import OAuthCredential
from src.repositories.booking_repository import BookingRepository
from src.repositories.room_repository import RoomRepository
from src.services.calendar_service import CalendarCache
from src.services.cloudbeds_service import CloudbedsService, CloudbedsServiceError

logger = logging.getLogger(__name__)

# Sync interval in seconds (5 minutes)
SYNC_INTERVAL_SECONDS = 300


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
    ) -> None:
        """Initialize SyncService.

        Args:
            session: Async database session.
            calendar_cache: Optional calendar cache to invalidate on sync.
        """
        self._session = session
        self._calendar_cache = calendar_cache
        self._booking_repo = BookingRepository(session)
        self._room_repo = RoomRepository(session)

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
            # Update sync error status
            error_msg = str(e)
            listing.last_sync_error = error_msg
            listing.last_sync_at = datetime.now(UTC)
            logger.error(
                "Sync failed for listing %s: %s",
                listing.cloudbeds_id,
                error_msg,
            )
            raise SyncServiceError(error_msg) from e

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

        for reservation in reservations:
            cloudbeds_booking_id = reservation.get("id") or reservation.get(
                "reservationID"
            )
            if not cloudbeds_booking_id:
                logger.warning("Skipping reservation with no ID")
                continue

            # Track the base reservation ID
            seen_reservation_ids.add(str(cloudbeds_booking_id))

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

        # If no rooms specified, create booking without room association
        if not cloudbeds_room_ids:
            booking_id = str(cloudbeds_booking_id)
            booking_ids.add(booking_id)
            was_created = await self._upsert_single_booking(
                listing, booking_id, None, booking_data
            )
            if was_created:
                inserted += 1
            else:
                updated += 1
        else:
            # Create a booking for EACH room in the reservation
            for cloudbeds_room_id in cloudbeds_room_ids:
                # Use composite booking ID for multi-room reservations
                # Use "::" delimiter to avoid ambiguity with IDs containing hyphens
                booking_id = (
                    f"{cloudbeds_booking_id}::{cloudbeds_room_id}"
                    if len(cloudbeds_room_ids) > 1
                    else str(cloudbeds_booking_id)
                )
                booking_ids.add(booking_id)

                room = await self._room_repo.get_by_cloudbeds_id(
                    listing.id, cloudbeds_room_id
                )
                room_id: int | None = None
                if room:
                    room_id = room.id
                else:
                    logger.warning(
                        "Room %s not found for booking %s - booking will not "
                        "appear in room calendars. Sync rooms first.",
                        cloudbeds_room_id,
                        cloudbeds_booking_id,
                    )

                was_created = await self._upsert_single_booking(
                    listing, booking_id, room_id, booking_data
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
        if "::" in booking_id:
            return booking_id.split("::")[0]
        return booking_id

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

        # Extract phone last 4
        phone = reservation.get("guestPhone") or reservation.get("guestCellPhone")
        phone_last4 = CloudbedsService.extract_phone_last4(phone)

        # Parse dates
        check_in = self._parse_date(reservation.get("startDate"))
        check_out = self._parse_date(reservation.get("endDate"))

        # Extract status
        status = reservation.get("status", "confirmed").lower()
        if status not in ("confirmed", "checked_in", "checked_out", "cancelled"):
            status = "confirmed"

        # Extract ALL room IDs from reservation (T020)
        # Prefer nested rooms array (authoritative), fallback to top-level roomID
        cloudbeds_room_ids: list[str] = []
        rooms = reservation.get("rooms", [])
        if rooms and isinstance(rooms, list):
            # Extract room IDs from nested array (multi-room reservations)
            for room in rooms:
                if isinstance(room, dict):
                    room_id = room.get("roomID") or room.get("roomId")
                    if room_id:
                        cloudbeds_room_ids.append(str(room_id))
        if not cloudbeds_room_ids:
            # Fallback to top-level room ID (legacy single-room format)
            top_level_room_id = reservation.get("roomID") or reservation.get("roomId")
            if top_level_room_id:
                cloudbeds_room_ids.append(str(top_level_room_id))

        # Build custom data from available fields
        custom_data = {}
        custom_field_mapping = {
            "booking_notes": ["notes", "bookingNotes"],
            "arrival_time": ["arrivalTime", "estimatedArrivalTime"],
            "departure_time": ["departureTime"],
            "num_guests": ["guestsCount", "adults"],
            "room_type_name": ["roomTypeName", "roomType"],
            "source_name": ["sourceName", "source"],
            "special_requests": ["specialRequests"],
        }

        for field_name, keys in custom_field_mapping.items():
            for key in keys:
                if reservation.get(key):
                    custom_data[field_name] = str(reservation[key])
                    break

        return {
            "guest_name": guest_name,
            "guest_phone_last4": phone_last4,
            "check_in_date": check_in,
            "check_out_date": check_out,
            "status": status,
            "cloudbeds_room_ids": cloudbeds_room_ids,
            "custom_data": custom_data if custom_data else None,
        }

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
