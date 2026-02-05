# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for settings API endpoints."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.system_settings import DEFAULT_SYNC_INTERVAL_MINUTES


@pytest.fixture
async def settings_engine():
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
async def settings_session(settings_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        settings_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def settings_app(settings_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        settings_engine,
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


class TestSettingsAPI:
    """Integration tests for settings API."""

    @pytest.mark.asyncio
    async def test_get_settings_default(self, settings_app):
        """Test getting settings returns defaults when no settings exist."""
        async with AsyncClient(
            transport=ASGITransport(app=settings_app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/settings",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["sync_interval_minutes"] == DEFAULT_SYNC_INTERVAL_MINUTES
        # ical_base_url should be present (uses request base_url in standalone mode)
        assert "ical_base_url" in data

    @pytest.mark.asyncio
    async def test_update_sync_interval(self, settings_app):
        """Test updating sync interval."""
        async with AsyncClient(
            transport=ASGITransport(app=settings_app),
            base_url="http://test",
        ) as client:
            response = await client.put(
                "/api/settings/sync-interval",
                json={"interval_minutes": 10},
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["interval_minutes"] == 10
        assert "message" in data

    @pytest.mark.asyncio
    async def test_update_sync_interval_persists(self, settings_app):
        """Test that updated sync interval is persisted."""
        async with AsyncClient(
            transport=ASGITransport(app=settings_app),
            base_url="http://test",
        ) as client:
            # Update interval
            await client.put(
                "/api/settings/sync-interval",
                json={"interval_minutes": 15},
                headers={"Authorization": "Bearer test"},
            )

            # Verify it's persisted
            response = await client.get(
                "/api/settings",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["sync_interval_minutes"] == 15

    @pytest.mark.asyncio
    async def test_update_sync_interval_minimum(self, settings_app):
        """Test updating sync interval to minimum value."""
        async with AsyncClient(
            transport=ASGITransport(app=settings_app),
            base_url="http://test",
        ) as client:
            response = await client.put(
                "/api/settings/sync-interval",
                json={"interval_minutes": 1},
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        assert response.json()["interval_minutes"] == 1

    @pytest.mark.asyncio
    async def test_update_sync_interval_maximum(self, settings_app):
        """Test updating sync interval to maximum value."""
        async with AsyncClient(
            transport=ASGITransport(app=settings_app),
            base_url="http://test",
        ) as client:
            response = await client.put(
                "/api/settings/sync-interval",
                json={"interval_minutes": 60},
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        assert response.json()["interval_minutes"] == 60

    @pytest.mark.asyncio
    async def test_update_sync_interval_below_minimum(self, settings_app):
        """Test updating sync interval below minimum fails."""
        async with AsyncClient(
            transport=ASGITransport(app=settings_app),
            base_url="http://test",
        ) as client:
            response = await client.put(
                "/api/settings/sync-interval",
                json={"interval_minutes": 0},
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_update_sync_interval_above_maximum(self, settings_app):
        """Test updating sync interval above maximum fails."""
        async with AsyncClient(
            transport=ASGITransport(app=settings_app),
            base_url="http://test",
        ) as client:
            response = await client.put(
                "/api/settings/sync-interval",
                json={"interval_minutes": 61},
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_update_sync_interval_invalid_type(self, settings_app):
        """Test updating sync interval with invalid type fails."""
        async with AsyncClient(
            transport=ASGITransport(app=settings_app),
            base_url="http://test",
        ) as client:
            response = await client.put(
                "/api/settings/sync-interval",
                json={"interval_minutes": "ten"},
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 422  # Validation error
