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

        for reservation in reservations:
            cloudbeds_booking_id = reservation.get("id") or reservation.get(
                "reservationID"
            )
            if not cloudbeds_booking_id:
                logger.warning("Skipping reservation with no ID")
                continue

            seen_booking_ids.add(str(cloudbeds_booking_id))

            # Extract booking data
            booking_data = self._extract_booking_data(reservation)

            # Skip reservations with invalid dates (T088)
            if not booking_data["check_in_date"] or not booking_data["check_out_date"]:
                logger.warning(
                    "Skipping reservation %s with invalid dates", cloudbeds_booking_id
                )
                continue

            # Look up room by cloudbeds_room_id if present (T020)
            room_id: int | None = None
            cloudbeds_room_id = booking_data.get("cloudbeds_room_id")
            if cloudbeds_room_id:
                room = await self._room_repo.get_by_cloudbeds_id(
                    listing.id, cloudbeds_room_id
                )
                if room:
                    room_id = room.id
                else:
                    logger.warning(
                        "Room %s not found for booking %s - booking will not "
                        "appear in room calendars. Sync rooms first.",
                        cloudbeds_room_id,
                        cloudbeds_booking_id,
                    )

            # Create Booking entity for upsert
            booking = Booking(
                listing_id=listing.id,
                room_id=room_id,
                cloudbeds_booking_id=str(cloudbeds_booking_id),
                guest_name=booking_data["guest_name"],
                guest_phone_last4=booking_data["guest_phone_last4"],
                check_in_date=booking_data["check_in_date"],
                check_out_date=booking_data["check_out_date"],
                status=booking_data["status"],
                custom_data=booking_data["custom_data"],
            )

            # Upsert booking
            _, was_created = await self._booking_repo.upsert(booking)

            if was_created:
                counts["inserted"] += 1
            else:
                counts["updated"] += 1

        # Mark cancelled bookings (not in current fetch)
        existing_bookings = await self._booking_repo.get_for_listing(listing.id)
        for existing_booking in existing_bookings:
            if (
                existing_booking.cloudbeds_booking_id not in seen_booking_ids
                and existing_booking.status != "cancelled"
            ):
                await self._booking_repo.mark_cancelled(existing_booking)
                counts["cancelled"] += 1

        # Invalidate calendar cache if any changes
        # Use prefix invalidation to clear all room-level caches for this listing
        if self._calendar_cache and sum(counts.values()) > 0:
            self._calendar_cache.invalidate_prefix(listing.ical_url_slug)
            logger.debug("Invalidated cache for listing %s", listing.ical_url_slug)

        await self._session.commit()

        logger.info(
            "Synced listing %s: %d inserted, %d updated, %d cancelled",
            listing.cloudbeds_id,
            counts["inserted"],
            counts["updated"],
            counts["cancelled"],
        )

        return counts

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

        # Extract room ID from reservation (T020)
        # Room ID can be at top level or in nested rooms array
        cloudbeds_room_id = reservation.get("roomID") or reservation.get("roomId")
        if not cloudbeds_room_id:
            # Check nested rooms array - use first room's ID
            rooms = reservation.get("rooms", [])
            if rooms and isinstance(rooms, list) and len(rooms) > 0:
                first_room = rooms[0]
                if isinstance(first_room, dict):
                    cloudbeds_room_id = first_room.get("roomID") or first_room.get(
                        "roomId"
                    )
                # Log warning if reservation spans multiple rooms
                if len(rooms) > 1:
                    res_id = reservation.get("reservationID", "unknown")
                    logger.warning(
                        "Reservation %s has %d rooms - only first room will be linked",
                        res_id,
                        len(rooms),
                    )
        if cloudbeds_room_id:
            cloudbeds_room_id = str(cloudbeds_room_id)

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
            "cloudbeds_room_id": cloudbeds_room_id,
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
