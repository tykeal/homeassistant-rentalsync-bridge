# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ListingRepository."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base
from src.models.listing import Listing
from src.repositories.listing_repository import ListingRepository


@pytest.fixture
async def repo_engine():
    """Create test database engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def repo_session(repo_engine):
    """Create test database session."""
    session_factory = async_sessionmaker(
        repo_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


class TestGenerateUniqueSlug:
    """Tests for generate_unique_slug method."""

    @pytest.mark.asyncio
    async def test_generates_slug_from_name(self, repo_session):
        """Test basic slug generation from name."""
        repo = ListingRepository(repo_session)
        slug = await repo.generate_unique_slug("Beach House")
        assert slug == "beach-house"

    @pytest.mark.asyncio
    async def test_handles_special_characters(self, repo_session):
        """Test slug generation removes special characters."""
        repo = ListingRepository(repo_session)
        slug = await repo.generate_unique_slug("Beach House #1!")
        assert "beach-house" in slug
        assert "#" not in slug
        assert "!" not in slug

    @pytest.mark.asyncio
    async def test_detects_collision_adds_suffix(self, repo_session):
        """Test that collision detection adds random suffix."""
        # Create existing listing with slug
        existing = Listing(
            cloudbeds_id="PROP1",
            name="Beach House",
            ical_url_slug="beach-house",
            enabled=True,
            sync_enabled=True,
        )
        repo_session.add(existing)
        await repo_session.commit()

        repo = ListingRepository(repo_session)
        slug = await repo.generate_unique_slug("Beach House")

        # Should have suffix due to collision
        assert slug.startswith("beach-house-")
        assert len(slug) > len("beach-house")
        assert slug != "beach-house"

    @pytest.mark.asyncio
    async def test_unique_slug_is_different_from_existing(self, repo_session):
        """Test generated slug is unique."""
        existing = Listing(
            cloudbeds_id="PROP1",
            name="Mountain Cabin",
            ical_url_slug="mountain-cabin",
            enabled=True,
            sync_enabled=True,
        )
        repo_session.add(existing)
        await repo_session.commit()

        repo = ListingRepository(repo_session)
        slug = await repo.generate_unique_slug("Mountain Cabin")

        # Verify it's different and won't collide
        existing_check = await repo.get_by_slug(slug)
        assert existing_check is None or existing_check.ical_url_slug != slug


class TestCountEnabled:
    """Tests for count_enabled method."""

    @pytest.mark.asyncio
    async def test_count_enabled_empty(self, repo_session):
        """Test count with no listings."""
        repo = ListingRepository(repo_session)
        count = await repo.count_enabled()
        assert count == 0

    @pytest.mark.asyncio
    async def test_count_enabled_mixed(self, repo_session):
        """Test count with mixed enabled/disabled listings."""
        listings = [
            Listing(
                cloudbeds_id="PROP1",
                name="Enabled 1",
                ical_url_slug="enabled-1",
                enabled=True,
                sync_enabled=True,
            ),
            Listing(
                cloudbeds_id="PROP2",
                name="Enabled 2",
                ical_url_slug="enabled-2",
                enabled=True,
                sync_enabled=True,
            ),
            Listing(
                cloudbeds_id="PROP3",
                name="Disabled",
                ical_url_slug="disabled",
                enabled=False,
                sync_enabled=False,
            ),
        ]
        for listing in listings:
            repo_session.add(listing)
        await repo_session.commit()

        repo = ListingRepository(repo_session)
        count = await repo.count_enabled()
        assert count == 2
