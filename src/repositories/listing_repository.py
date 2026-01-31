# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Repository for Listing database operations."""

import secrets
import string
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.listing import Listing

# Maximum listings per deployment
MAX_LISTINGS = 50


class ListingRepository:
    """Repository for Listing CRUD operations.

    Provides async database operations for Listing entities with
    proper query optimization and constraint enforcement.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session.
        """
        self._session = session

    async def get_by_id(self, listing_id: int) -> Listing | None:
        """Get listing by ID.

        Args:
            listing_id: Listing primary key.

        Returns:
            Listing if found, None otherwise.
        """
        result = await self._session.execute(
            select(Listing).where(Listing.id == listing_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Listing | None:
        """Get listing by iCal URL slug.

        Args:
            slug: URL-safe identifier for iCal endpoint.

        Returns:
            Listing if found, None otherwise.
        """
        result = await self._session.execute(
            select(Listing).where(Listing.ical_url_slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_by_cloudbeds_id(self, cloudbeds_id: str) -> Listing | None:
        """Get listing by Cloudbeds property ID.

        Args:
            cloudbeds_id: Cloudbeds property identifier.

        Returns:
            Listing if found, None otherwise.
        """
        result = await self._session.execute(
            select(Listing).where(Listing.cloudbeds_id == cloudbeds_id)
        )
        return result.scalar_one_or_none()

    async def get_all(self) -> Sequence[Listing]:
        """Get all listings.

        Returns:
            Sequence of all listings.
        """
        result = await self._session.execute(select(Listing).order_by(Listing.name))
        return result.scalars().all()

    async def get_enabled(self) -> Sequence[Listing]:
        """Get all enabled listings.

        Returns:
            Sequence of enabled listings.
        """
        result = await self._session.execute(
            select(Listing).where(Listing.enabled.is_(True)).order_by(Listing.name)
        )
        return result.scalars().all()

    async def get_sync_enabled(self) -> Sequence[Listing]:
        """Get all listings with sync enabled.

        Returns:
            Sequence of sync-enabled listings.
        """
        result = await self._session.execute(
            select(Listing)
            .where(Listing.enabled.is_(True), Listing.sync_enabled.is_(True))
            .order_by(Listing.name)
        )
        return result.scalars().all()

    async def count(self) -> int:
        """Count total listings.

        Returns:
            Total number of listings.
        """
        result = await self._session.execute(select(Listing))
        return len(result.scalars().all())

    async def count_enabled(self) -> int:
        """Count enabled listings.

        Returns:
            Number of enabled listings.
        """
        result = await self._session.execute(
            select(Listing).where(Listing.enabled.is_(True))
        )
        return len(result.scalars().all())

    async def create(self, listing: Listing) -> Listing:
        """Create a new listing.

        Args:
            listing: Listing entity to create.

        Returns:
            Created listing with ID.

        Raises:
            ValueError: If max listings limit reached.
        """
        current_count = await self.count()
        if current_count >= MAX_LISTINGS:
            msg = f"Maximum number of listings ({MAX_LISTINGS}) reached"
            raise ValueError(msg)

        # Generate slug if not provided
        if not listing.ical_url_slug:
            listing.ical_url_slug = await self.generate_unique_slug(listing.name)

        self._session.add(listing)
        await self._session.flush()
        await self._session.refresh(listing)
        return listing

    async def update(self, listing: Listing) -> Listing:
        """Update an existing listing.

        Args:
            listing: Listing entity with updates.

        Returns:
            Updated listing.
        """
        await self._session.flush()
        await self._session.refresh(listing)
        return listing

    async def delete(self, listing: Listing) -> None:
        """Delete a listing.

        Args:
            listing: Listing entity to delete.
        """
        await self._session.delete(listing)
        await self._session.flush()

    async def generate_unique_slug(self, name: str) -> str:
        """Generate a unique URL-safe slug from listing name.

        Args:
            name: Listing display name.

        Returns:
            Unique URL-safe slug.
        """
        # Create base slug from name
        base_slug = self._slugify(name)

        # Check if slug exists
        existing = await self.get_by_slug(base_slug)
        if existing is None:
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
        return slug[:100] if slug else "listing"
