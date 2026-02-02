# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for admin endpoint authentication."""

import os
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app


@pytest.fixture
async def auth_engine():
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
async def auth_app_production(auth_engine) -> AsyncGenerator:
    """Create test app in production mode (auth required)."""
    from src.config import get_settings

    # Clear settings cache
    get_settings.cache_clear()

    with patch.dict(os.environ, {"STANDALONE_MODE": "false"}, clear=False):
        # Clear again after env change
        get_settings.cache_clear()
        app = create_app()
        session_factory = async_sessionmaker(
            auth_engine,
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
        # Clear cache after test
        get_settings.cache_clear()


class TestAdminEndpointAuth:
    """Tests for admin endpoint authentication in production mode."""

    @pytest.mark.asyncio
    async def test_oauth_status_requires_auth(self, auth_app_production):
        """Test OAuth status endpoint requires authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=auth_app_production), base_url="http://test"
        ) as client:
            response = await client.get("/api/oauth/status")

        assert response.status_code == 401
        assert "Authentication required" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_oauth_status_with_auth(self, auth_app_production):
        """Test OAuth status endpoint with HA auth header."""
        async with AsyncClient(
            transport=ASGITransport(app=auth_app_production), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/oauth/status",
                headers={"X-Hass-User-Id": "test-user-123"},
            )

        # Should succeed with auth header (200 even if not configured)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_listings_requires_auth(self, auth_app_production):
        """Test listings endpoint requires authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=auth_app_production), base_url="http://test"
        ) as client:
            response = await client.get("/api/listings")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_status_requires_auth(self, auth_app_production):
        """Test status endpoint requires authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=auth_app_production), base_url="http://test"
        ) as client:
            response = await client.get("/api/status")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_health_is_public(self, auth_app_production):
        """Test health endpoint is public (no auth required)."""
        async with AsyncClient(
            transport=ASGITransport(app=auth_app_production), base_url="http://test"
        ) as client:
            response = await client.get("/health")

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_ical_is_public(self, auth_app_production):
        """Test iCal endpoints are public (no auth required)."""
        async with AsyncClient(
            transport=ASGITransport(app=auth_app_production), base_url="http://test"
        ) as client:
            # Test new room-level endpoint format (returns 404 for nonexistent)
            response = await client.get("/ical/nonexistent/room.ics")

        # 404 is expected for nonexistent, but not 401
        assert response.status_code == 404
