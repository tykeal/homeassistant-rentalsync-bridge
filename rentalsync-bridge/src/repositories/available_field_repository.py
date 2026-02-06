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
    r"ID$",  # ID fields (propertyID, reservationID, etc.)
    r"^id$",
]

# Fields that should always be available (built-in)
BUILTIN_FIELDS: dict[str, str] = {
    "guest_phone_last4": "Guest Phone (Last 4 Digits)",
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


def _should_exclude_field(field_key: str) -> bool:
    """Check if a field should be excluded from discovery.

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
        if _should_exclude_field(field_key):
            return None

        existing = await self.get_by_field_key(listing_id, field_key)
        now = datetime.now(UTC)

        if existing:
            existing.last_seen_at = now
            if sample_value and not existing.sample_value:
                existing.sample_value = sample_value[:500]
            await self._session.flush()
            return existing

        field = AvailableField(
            listing_id=listing_id,
            field_key=field_key,
            display_name=_camel_to_display(field_key),
            sample_value=sample_value[:500] if sample_value else None,
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
    ) -> list[AvailableField]:
        """Discover and store fields from a reservation.

        Args:
            listing_id: Listing ID to associate fields with.
            reservation: Reservation dict from Cloudbeds API.

        Returns:
            List of discovered/updated fields.
        """
        discovered: list[AvailableField] = []

        for key, value in reservation.items():
            # Skip None, empty, and complex values (dicts, lists)
            if value is None or value == "" or isinstance(value, (dict, list)):
                continue

            sample = str(value)[:500] if value else None
            field = await self.upsert_field(listing_id, key, sample)
            if field:
                discovered.append(field)

        return discovered

    async def get_all_field_keys(self, listing_id: int) -> dict[str, str]:
        """Get all available field keys and display names for a listing.

        Combines dynamically discovered fields with built-in fields.

        Args:
            listing_id: Listing ID to get fields for.

        Returns:
            Dictionary mapping field_key to display_name.
        """
        fields = await self.get_for_listing(listing_id)
        result = {f.field_key: f.display_name for f in fields}
        # Add built-in fields
        result.update(BUILTIN_FIELDS)
        return result
