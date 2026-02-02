# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Repository for Room database operations."""

import secrets
import string
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.listing import Listing
from src.models.room import Room


class RoomRepository:
    """Repository for Room CRUD operations.

    Provides async database operations for Room entities with
    proper query optimization and constraint enforcement.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_id(self, room_id: int) -> Room | None:
        """Get room by ID.

        Args:
            room_id: Room primary key.

        Returns:
            Room if found, None otherwise.
        """
        result = await self._session.execute(select(Room).where(Room.id == room_id))
        return result.scalar_one_or_none()

    async def get_by_listing_id(self, listing_id: int) -> Sequence[Room]:
        """Get all rooms for a listing.

        Args:
            listing_id: Listing primary key.

        Returns:
            Sequence of rooms for the listing.
        """
        result = await self._session.execute(
            select(Room).where(Room.listing_id == listing_id).order_by(Room.room_name)
        )
        return result.scalars().all()

    async def get_enabled_by_listing_id(self, listing_id: int) -> Sequence[Room]:
        """Get enabled rooms for a listing.

        Args:
            listing_id: Listing primary key.

        Returns:
            Sequence of enabled rooms for the listing.
        """
        result = await self._session.execute(
            select(Room)
            .where(Room.listing_id == listing_id, Room.enabled.is_(True))
            .order_by(Room.room_name)
        )
        return result.scalars().all()

    async def get_by_slug(self, listing_slug: str, room_slug: str) -> Room | None:
        """Get room by listing slug and room slug.

        Args:
            listing_slug: Listing iCal URL slug.
            room_slug: Room iCal URL slug.

        Returns:
            Room if found, None otherwise.
        """
        result = await self._session.execute(
            select(Room)
            .join(Listing)
            .where(
                Listing.ical_url_slug == listing_slug, Room.ical_url_slug == room_slug
            )
        )
        return result.scalar_one_or_none()

    async def get_by_cloudbeds_id(
        self, listing_id: int, cloudbeds_room_id: str
    ) -> Room | None:
        """Get room by Cloudbeds room ID within a listing.

        Args:
            listing_id: Listing primary key.
            cloudbeds_room_id: Cloudbeds room identifier.

        Returns:
            Room if found, None otherwise.
        """
        result = await self._session.execute(
            select(Room).where(
                Room.listing_id == listing_id,
                Room.cloudbeds_room_id == cloudbeds_room_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert_room(
        self,
        listing_id: int,
        cloudbeds_room_id: str,
        room_name: str,
        room_type_name: str | None = None,
    ) -> Room:
        """Create or update a room.

        If a room with the given cloudbeds_room_id exists for the listing,
        it will be updated. Otherwise, a new room will be created.

        Args:
            listing_id: Listing primary key.
            cloudbeds_room_id: Cloudbeds room identifier.
            room_name: Room display name.
            room_type_name: Optional room type name.

        Returns:
            Created or updated room.
        """
        existing = await self.get_by_cloudbeds_id(listing_id, cloudbeds_room_id)

        if existing:
            # Update existing room
            existing.room_name = room_name
            existing.room_type_name = room_type_name
            await self._session.flush()
            await self._session.refresh(existing)
            return existing

        # Create new room
        return await self.create_room(
            listing_id, cloudbeds_room_id, room_name, room_type_name
        )

    async def create_room(
        self,
        listing_id: int,
        cloudbeds_room_id: str,
        room_name: str,
        room_type_name: str | None = None,
    ) -> Room:
        """Create a new room (no existence check).

        Args:
            listing_id: Listing primary key.
            cloudbeds_room_id: Cloudbeds room identifier.
            room_name: Room display name.
            room_type_name: Optional room type name.

        Returns:
            Created room.
        """
        slug = await self.generate_unique_slug(listing_id, room_name)
        room = Room(
            listing_id=listing_id,
            cloudbeds_room_id=cloudbeds_room_id,
            room_name=room_name,
            room_type_name=room_type_name,
            ical_url_slug=slug,
            enabled=True,
        )
        self._session.add(room)
        await self._session.flush()
        await self._session.refresh(room)
        return room

    async def toggle_room_enabled(self, room_id: int, enabled: bool) -> Room | None:
        """Toggle room enabled status.

        Args:
            room_id: Room primary key.
            enabled: New enabled status.

        Returns:
            Updated room if found, None otherwise.
        """
        room = await self.get_by_id(room_id)
        if not room:
            return None

        room.enabled = enabled
        await self._session.flush()
        await self._session.refresh(room)
        return room

    async def update_slug(self, room_id: int, new_slug: str) -> Room | None:
        """Update room iCal URL slug.

        Args:
            room_id: Room primary key.
            new_slug: New URL slug.

        Returns:
            Updated room if found, None otherwise.
        """
        room = await self.get_by_id(room_id)
        if not room:
            return None

        room.ical_url_slug = new_slug
        await self._session.flush()
        await self._session.refresh(room)
        return room

    async def get_all_slugs_for_listing(self, listing_id: int) -> set[str]:
        """Get all existing room slugs for a listing.

        Args:
            listing_id: Listing primary key.

        Returns:
            Set of all slugs for the listing.
        """
        result = await self._session.execute(
            select(Room.ical_url_slug).where(Room.listing_id == listing_id)
        )
        return {slug for (slug,) in result.all() if slug}

    async def generate_unique_slug(self, listing_id: int, name: str) -> str:
        """Generate a unique URL-safe slug from room name.

        Args:
            listing_id: Listing primary key.
            name: Room display name.

        Returns:
            Unique URL-safe slug for the room within the listing.
        """
        base_slug = self._slugify(name)
        existing_slugs = await self.get_all_slugs_for_listing(listing_id)

        if base_slug not in existing_slugs:
            return base_slug

        # Add random suffix if collision
        suffix = "".join(secrets.choice(string.ascii_lowercase) for _ in range(6))
        return f"{base_slug}-{suffix}"

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to URL-safe slug.

        Args:
            text: Input text.

        Returns:
            URL-safe lowercase slug.
        """
        # Convert to lowercase and replace spaces with hyphens
        slug = text.lower().strip()
        slug = slug.replace(" ", "-")

        # Keep only alphanumeric and hyphens
        slug = "".join(c for c in slug if c.isalnum() or c == "-")

        # Remove multiple consecutive hyphens
        while "--" in slug:
            slug = slug.replace("--", "-")

        # Remove leading/trailing hyphens
        slug = slug.strip("-")

        # Truncate to max length
        return slug[:100] if slug else "room"
