# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Repository for CustomField database operations."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.custom_field import CustomField

# Predefined custom fields available from Cloudbeds API
AVAILABLE_FIELDS: dict[str, str] = {
    "booking_notes": "Booking Notes",
    "arrival_time": "Arrival Time",
    "departure_time": "Departure Time",
    "num_guests": "Number of Guests",
    "room_type_name": "Room Type",
    "source_name": "Booking Source",
    "special_requests": "Special Requests",
    "estimated_arrival": "Estimated Arrival",
    "guest_phone_last4": "Guest Phone (Last 4 Digits)",
}


class CustomFieldRepository:
    """Repository for CustomField CRUD operations.

    Provides async database operations for CustomField entities with
    support for the predefined field list from Cloudbeds API.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_id(self, field_id: int) -> CustomField | None:
        """Get custom field by ID.

        Args:
            field_id: CustomField primary key.

        Returns:
            CustomField if found, None otherwise.
        """
        result = await self._session.execute(
            select(CustomField).where(CustomField.id == field_id)
        )
        return result.scalar_one_or_none()

    async def get_for_listing(self, listing_id: int) -> Sequence[CustomField]:
        """Get all custom fields for a listing.

        Args:
            listing_id: Listing ID to filter by.

        Returns:
            Sequence of custom fields ordered by sort_order.
        """
        result = await self._session.execute(
            select(CustomField)
            .where(CustomField.listing_id == listing_id)
            .order_by(CustomField.sort_order, CustomField.field_name)
        )
        return result.scalars().all()

    async def get_enabled_for_listing(self, listing_id: int) -> Sequence[CustomField]:
        """Get enabled custom fields for a listing.

        Args:
            listing_id: Listing ID to filter by.

        Returns:
            Sequence of enabled custom fields ordered by sort_order.
        """
        result = await self._session.execute(
            select(CustomField)
            .where(
                CustomField.listing_id == listing_id,
                CustomField.enabled.is_(True),
            )
            .order_by(CustomField.sort_order, CustomField.field_name)
        )
        return result.scalars().all()

    async def get_by_field_name(
        self, listing_id: int, field_name: str
    ) -> CustomField | None:
        """Get custom field by listing and field name.

        Args:
            listing_id: Listing ID to filter by.
            field_name: Field name to look up.

        Returns:
            CustomField if found, None otherwise.
        """
        result = await self._session.execute(
            select(CustomField).where(
                CustomField.listing_id == listing_id,
                CustomField.field_name == field_name,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, field: CustomField) -> CustomField:
        """Create a new custom field.

        Args:
            field: CustomField entity to create.

        Returns:
            Created custom field with ID.

        Raises:
            ValueError: If field_name is not in allowed list.
        """
        if field.field_name not in AVAILABLE_FIELDS:
            allowed = ", ".join(sorted(AVAILABLE_FIELDS.keys()))
            msg = f"Invalid field_name '{field.field_name}'. Allowed: {allowed}"
            raise ValueError(msg)

        self._session.add(field)
        await self._session.flush()
        await self._session.refresh(field)
        return field

    async def update(self, field: CustomField) -> CustomField:
        """Update an existing custom field.

        Args:
            field: CustomField entity with updates.

        Returns:
            Updated custom field.
        """
        await self._session.flush()
        await self._session.refresh(field)
        return field

    async def delete(self, field: CustomField) -> None:
        """Delete a custom field.

        Args:
            field: CustomField entity to delete.
        """
        await self._session.delete(field)
        await self._session.flush()

    async def create_defaults_for_listing(self, listing_id: int) -> list[CustomField]:
        """Create default custom fields for a new listing.

        Creates the default set of custom fields when a listing is enabled:
        - booking_notes (enabled by default)
        - guest_phone_last4 (enabled by default)

        Args:
            listing_id: Listing ID to create fields for.

        Returns:
            List of created custom fields.
        """
        defaults = [
            ("booking_notes", "Booking Notes", True, 0),
            ("guest_phone_last4", "Guest Phone (Last 4 Digits)", True, 1),
        ]

        created: list[CustomField] = []
        for field_name, display_label, enabled, sort_order in defaults:
            # Check if already exists
            existing = await self.get_by_field_name(listing_id, field_name)
            if existing is None:
                field = CustomField(
                    listing_id=listing_id,
                    field_name=field_name,
                    display_label=display_label,
                    enabled=enabled,
                    sort_order=sort_order,
                )
                self._session.add(field)
                await self._session.flush()
                await self._session.refresh(field)
                created.append(field)

        return created

    @staticmethod
    def get_available_fields() -> dict[str, str]:
        """Get dictionary of available custom field names and labels.

        Returns:
            Dictionary mapping field_name to display_label.
        """
        return AVAILABLE_FIELDS.copy()
