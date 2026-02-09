# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Repository for AvailableField database operations."""

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.available_field import AvailableField

# Fields to exclude from discovery (internal/system fields)
EXCLUDED_FIELD_PATTERNS = [
    r"^_",  # Internal fields starting with underscore
    r"[A-Z]d$",  # ID fields ending in Id (camelCase: reservationId, roomId)
    r"ID$",  # ID fields ending in ID (ALLCAPS: propertyID, roomID)
    r"^id$",  # Exact match for "id" field
]

# Fields that should always be available (built-in/computed)
BUILTIN_FIELDS: dict[str, str] = {
    "guest_phone_last4": "Guest Phone (Last 4 Digits)",
}

# Default Cloudbeds fields - always available even without reservations
# These are common fields returned by the Cloudbeds API
DEFAULT_CLOUDBEDS_FIELDS: dict[str, str] = {
    "guestName": "Guest Name",
    "guestFirstName": "Guest First Name",
    "guestLastName": "Guest Last Name",
    "guestEmail": "Guest Email",
    "guestPhone": "Guest Phone",
    "guestCountry": "Guest Country",
    "notes": "Booking Notes",
    "status": "Booking Status",
    "sourceName": "Booking Source",
    "startDate": "Check-in Date",
    "endDate": "Check-out Date",
    "dateCreated": "Date Created",
    "adults": "Number of Adults",
    "children": "Number of Children",
    "balance": "Balance Due",
    "total": "Total Amount",
    "paid": "Amount Paid",
    "roomTypeName": "Room Type",
    "roomName": "Room Name",
    "confirmationCode": "Confirmation Code",
    "estimatedArrivalTime": "Estimated Arrival Time",
}


def _camel_to_display(name: str) -> str:
    """Convert camelCase to Display Name.

    Args:
        name: camelCase field name.

    Returns:
        Human-readable display name.
    """
    # Insert space before uppercase letters
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    # Capitalize first letter of each word
    return spaced.title()


def should_exclude_field(field_key: str) -> bool:
    """Check if a field should be excluded from discovery.

    Excludes ID fields (reservationId, propertyID, etc.) but not fields
    that happen to end in 'id' like 'paid'.

    Args:
        field_key: Field key to check.

    Returns:
        True if field should be excluded.
    """
    return any(re.search(pattern, field_key) for pattern in EXCLUDED_FIELD_PATTERNS)


class AvailableFieldRepository:
    """Repository for AvailableField CRUD operations.

    Provides async database operations for dynamically discovered
    Cloudbeds fields.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_for_listing(self, listing_id: int) -> Sequence[AvailableField]:
        """Get all available fields for a listing.

        Args:
            listing_id: Listing ID to filter by.

        Returns:
            Sequence of available fields ordered by display_name.
        """
        result = await self._session.execute(
            select(AvailableField)
            .where(AvailableField.listing_id == listing_id)
            .order_by(AvailableField.display_name)
        )
        return result.scalars().all()

    async def get_by_field_key(
        self, listing_id: int, field_key: str
    ) -> AvailableField | None:
        """Get available field by listing and field key.

        Args:
            listing_id: Listing ID to filter by.
            field_key: Field key to look up.

        Returns:
            AvailableField if found, None otherwise.
        """
        result = await self._session.execute(
            select(AvailableField).where(
                AvailableField.listing_id == listing_id,
                AvailableField.field_key == field_key,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_field(
        self,
        listing_id: int,
        field_key: str,
        sample_value: str | None = None,
    ) -> AvailableField | None:
        """Create or update an available field.

        Args:
            listing_id: Listing ID to create field for.
            field_key: Field key from Cloudbeds.
            sample_value: Sample value for display.

        Returns:
            Created/updated field, or None if field should be excluded.
        """
        if should_exclude_field(field_key):
            return None

        existing = await self.get_by_field_key(listing_id, field_key)
        now = datetime.now(UTC)

        if existing:
            existing.last_seen_at = now
            # Only update sample if we have a new value and existing is empty
            # Use explicit None/empty check to preserve falsy values like "0"
            if (
                sample_value is not None
                and sample_value != ""
                and (existing.sample_value is None or existing.sample_value == "")
            ):
                existing.sample_value = sample_value[:500]
            await self._session.flush()
            return existing

        # Store sample_value preserving falsy values like "0" or "false"
        stored_sample = (
            sample_value[:500]
            if sample_value is not None and sample_value != ""
            else None
        )
        field = AvailableField(
            listing_id=listing_id,
            field_key=field_key,
            display_name=_camel_to_display(field_key),
            sample_value=stored_sample,
            discovered_at=now,
            last_seen_at=now,
        )
        self._session.add(field)
        await self._session.flush()
        await self._session.refresh(field)
        return field

    async def discover_fields_from_reservation(
        self,
        listing_id: int,
        reservation: dict,
        already_discovered: set[str] | None = None,
    ) -> list[AvailableField]:
        """Discover and store fields from a reservation.

        Discovers fields from both the top-level reservation and from
        the first room in the rooms array (if present) to capture
        room-specific fields like roomTypeName and roomName.

        Only the first room is inspected for field discovery since all
        rooms share the same schema - we only need to identify which
        field keys exist, not sample every room's values.

        Uses already_discovered set to skip fields already processed in
        this sync run, avoiding redundant database operations.

        Args:
            listing_id: Listing ID to associate fields with.
            reservation: Reservation dict from Cloudbeds API.
            already_discovered: Optional set of field keys already processed
                in this sync run. Will be updated with newly discovered keys.

        Returns:
            List of discovered/updated fields.
        """
        discovered: list[AvailableField] = []
        if already_discovered is None:
            already_discovered = set()

        # Discover from top-level reservation fields
        for key, value in reservation.items():
            # Skip already-discovered fields in this sync run
            if key in already_discovered:
                continue
            # Skip None, empty, and complex values (dicts, lists)
            if value is None or value == "" or isinstance(value, (dict, list)):
                continue

            # Value is already validated as not None/empty, safe to convert
            sample = str(value)[:500]
            field = await self.upsert_field(listing_id, key, sample)
            if field:
                discovered.append(field)
                already_discovered.add(key)

        # Also discover from first room in rooms array (if present)
        rooms = reservation.get("rooms", [])
        if rooms and isinstance(rooms, list) and len(rooms) > 0:
            first_room = rooms[0]
            if isinstance(first_room, dict):
                for key, value in first_room.items():
                    # Skip already-discovered fields in this sync run
                    if key in already_discovered:
                        continue
                    # Skip None, empty, and complex values
                    if value is None or value == "" or isinstance(value, (dict, list)):
                        continue

                    # Value is already validated as not None/empty, safe to convert
                    sample = str(value)[:500]
                    field = await self.upsert_field(listing_id, key, sample)
                    if field:
                        discovered.append(field)
                        already_discovered.add(key)

        return discovered

    async def get_all_field_keys(self, listing_id: int) -> dict[str, str]:
        """Get all available field keys and display names for a listing.

        Combines: default Cloudbeds fields + discovered fields + built-in fields.
        Discovered fields override defaults if they have the same key.

        Args:
            listing_id: Listing ID to get fields for.

        Returns:
            Dictionary mapping field_key to display_name.
        """
        # Start with default Cloudbeds fields
        result = DEFAULT_CLOUDBEDS_FIELDS.copy()
        # Add/override with discovered fields from actual data
        fields = await self.get_for_listing(listing_id)
        result.update({f.field_key: f.display_name for f in fields})
        # Add built-in computed fields
        result.update(BUILTIN_FIELDS)
        return result
