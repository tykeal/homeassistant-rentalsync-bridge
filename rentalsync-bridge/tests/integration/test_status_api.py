# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for status API endpoint."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.booking import Booking
from src.models.listing import Listing
from src.models.oauth_credential import OAuthCredential


@pytest.fixture
async def status_engine():
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
async def status_session(status_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        status_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def status_app(status_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        status_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


class TestSystemStatus:
    """Tests for GET /api/status endpoint."""

    @pytest.mark.asyncio
    async def test_status_unconfigured(self, status_app):
        """Test status when OAuth not configured."""
        async with AsyncClient(
            transport=ASGITransport(app=status_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/status",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unconfigured"
        assert data["oauth"]["configured"] is False
        assert data["oauth"]["connected"] is False
        assert data["listings"]["enabled"] == 0
        assert data["listings"]["total"] == 0
        assert data["bookings_count"] == 0

    @pytest.mark.asyncio
    async def test_status_healthy(self, status_app, status_session):
        """Test status when fully configured and connected."""
        # Add OAuth credential
        cred = OAuthCredential(client_id="test")
        cred.client_secret = "secret"
        cred.access_token = "access"
        cred.refresh_token = "refresh"
        cred.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        status_session.add(cred)

        # Add enabled listing
        listing = Listing(
            cloudbeds_id="PROP1",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
        )
        status_session.add(listing)
        await status_session.commit()
        await status_session.refresh(listing)

        # Add booking
        booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="BK001",
            guest_name="Test Guest",
            check_in_date=datetime.now(UTC),
            check_out_date=datetime.now(UTC) + timedelta(days=2),
            status="confirmed",
        )
        status_session.add(booking)
        await status_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=status_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/status",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["oauth"]["configured"] is True
        assert data["oauth"]["connected"] is True
        assert data["listings"]["enabled"] == 1
        assert data["listings"]["total"] == 1
        assert data["bookings_count"] == 1

    @pytest.mark.asyncio
    async def test_status_disconnected(self, status_app, status_session):
        """Test status when OAuth expired."""
        cred = OAuthCredential(client_id="test")
        cred.client_secret = "secret"
        cred.access_token = "access"
        cred.refresh_token = "refresh"
        cred.token_expires_at = datetime.now(UTC) - timedelta(hours=1)
        status_session.add(cred)
        await status_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=status_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/status",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "disconnected"
        assert data["oauth"]["configured"] is True
        assert data["oauth"]["connected"] is False

    @pytest.mark.asyncio
    async def test_status_includes_version(self, status_app):
        """Test status includes version."""
        async with AsyncClient(
            transport=ASGITransport(app=status_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/status",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert data["version"] == "0.1.0"

    @pytest.mark.asyncio
    async def test_status_includes_timestamp(self, status_app):
        """Test status includes timestamp."""
        async with AsyncClient(
            transport=ASGITransport(app=status_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/status",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert "timestamp" in data
