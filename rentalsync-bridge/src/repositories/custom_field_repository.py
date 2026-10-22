# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Repository for CustomField database operations."""

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.custom_field import CustomField
from src.repositories.available_field_repository import (
    BUILTIN_FIELDS,
    DEFAULT_CLOUDBEDS_FIELDS,
    AvailableFieldRepository,
    format_allowed_fields_message,
)
from src.utils.field_labels import camel_to_display, legacy_display_name


class CustomFieldRepository:
    """Repository for CustomField CRUD operations.

    Provides async database operations for CustomField entities with
    support for dynamically discovered fields from Cloudbeds API.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session
        self._available_field_repo = AvailableFieldRepository(session)

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

    async def get_available_fields_for_listing(self, listing_id: int) -> dict[str, str]:
        """Get available fields for a listing.

        Delegates to AvailableFieldRepository.get_all_field_keys() to ensure
        consistent field composition logic across the codebase.

        Args:
            listing_id: Listing ID to get fields for.

        Returns:
            Dictionary mapping field_key to display_name.
        """
        return await self._available_field_repo.get_all_field_keys(listing_id)

    async def create(self, field: CustomField) -> CustomField:
        """Create a new custom field.

        Args:
            field: CustomField entity to create.

        Returns:
            Created custom field with ID.

        Raises:
            ValueError: If field_name is not in available fields for the listing.
        """
        available = await self.get_available_fields_for_listing(field.listing_id)
        if field.field_name not in available:
            allowed = format_allowed_fields_message(available.keys())
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

    async def repair_derived_labels(
        self,
        listing_id: int,
        field_labels: Mapping[str, str],
    ) -> int:
        """Repair stored labels that were auto-generated, never edited.

        ``display_label`` is user-owned, so a label the user chose is
        never touched.  A label that is character-for-character what the
        pre-fix code would have generated from the field key (e.g. the
        mangled ``6 A 5 A 9 F 38050 Dfd 00134 Fe 27 E`` produced from a
        Guesty ObjectId — issue #127) demonstrably came from the UI's
        prefill rather than from a person, so it is safe to refresh it
        with the provider's real name.

        Args:
            listing_id: Listing whose configured fields to inspect.
            field_labels: Provider-supplied labels by field key.

        Returns:
            Number of labels repaired.
        """
        if not field_labels:
            return 0

        repaired = 0
        for field in await self.get_for_listing(listing_id):
            label = field_labels.get(field.field_name)
            if not label or label == field.display_label:
                continue
            derived = {
                legacy_display_name(field.field_name),
                camel_to_display(field.field_name),
            }
            if field.display_label in derived:
                field.display_label = label[:255]
                repaired += 1

        if repaired:
            await self._session.flush()
        return repaired

    async def create_defaults_for_listing(self, listing_id: int) -> list[CustomField]:
        """Create default custom fields for a new listing.

        Creates the built-in guest_phone_last4 field by default. Additional
        fields from DEFAULT_CLOUDBEDS_FIELDS are always available for
        configuration. Other dynamic fields will become discoverable after
        the first sync processes reservations from Cloudbeds.

        Args:
            listing_id: Listing ID to create fields for.

        Returns:
            List of created custom fields.
        """
        # Only create built-in fields as defaults
        # Other fields are dynamically discovered during sync
        defaults = [
            ("guest_phone_last4", "Guest Phone (Last 4 Digits)", True, 0),
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
    def get_builtin_fields() -> dict[str, str]:
        """Get dictionary of built-in custom field names and labels.

        These are computed/special fields always available.

        Returns:
            Dictionary mapping field_name to display_label.
        """
        return BUILTIN_FIELDS.copy()

    @staticmethod
    def get_default_cloudbeds_fields() -> dict[str, str]:
        """Get dictionary of default Cloudbeds field names and labels.

        These are common fields available even without sync data.

        Returns:
            Dictionary mapping field_name to display_label.
        """
        return DEFAULT_CLOUDBEDS_FIELDS.copy()
