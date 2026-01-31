# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for OAuth API endpoints."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.oauth_credential import OAuthCredential


@pytest.fixture
async def oauth_engine():
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
async def oauth_session(oauth_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        oauth_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def oauth_app(oauth_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        oauth_engine,
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


class TestOAuthStatus:
    """Tests for GET /api/oauth/status endpoint."""

    @pytest.mark.asyncio
    async def test_status_no_credentials(self, oauth_app):
        """Test status when no credentials configured."""
        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/oauth/status",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is False
        assert data["connected"] is False

    @pytest.mark.asyncio
    async def test_status_with_valid_credentials(self, oauth_app, oauth_session):
        """Test status with valid non-expired credentials."""
        cred = OAuthCredential(client_id="test_client")
        cred.client_secret = "secret"
        cred.access_token = "access"
        cred.refresh_token = "refresh"
        cred.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        oauth_session.add(cred)
        await oauth_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/oauth/status",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is True
        assert data["connected"] is True
        assert data["token_expired"] is False

    @pytest.mark.asyncio
    async def test_status_with_expired_credentials(self, oauth_app, oauth_session):
        """Test status with expired credentials."""
        cred = OAuthCredential(client_id="test_client")
        cred.client_secret = "secret"
        cred.access_token = "access"
        cred.refresh_token = "refresh"
        cred.token_expires_at = datetime.now(UTC) - timedelta(hours=1)
        oauth_session.add(cred)
        await oauth_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/oauth/status",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["configured"] is True
        assert data["connected"] is False
        assert data["token_expired"] is True


class TestOAuthConfigure:
    """Tests for POST /api/oauth/configure endpoint."""

    @pytest.mark.asyncio
    async def test_configure_new_credentials(self, oauth_app):
        """Test configuring new OAuth credentials."""
        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/oauth/configure",
                headers={"Authorization": "Bearer test"},
                json={
                    "client_id": "new_client",
                    "client_secret": "new_secret",
                    "access_token": "new_access",
                    "refresh_token": "new_refresh",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_configure_update_existing(self, oauth_app, oauth_session):
        """Test updating existing OAuth credentials."""
        cred = OAuthCredential(client_id="old_client")
        cred.client_secret = "old_secret"
        cred.access_token = "old_access"
        cred.refresh_token = "old_refresh"
        oauth_session.add(cred)
        await oauth_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/oauth/configure",
                headers={"Authorization": "Bearer test"},
                json={
                    "client_id": "updated_client",
                    "client_secret": "updated_secret",
                    "access_token": "updated_access",
                    "refresh_token": "updated_refresh",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_configure_validation_error(self, oauth_app):
        """Test validation error for missing fields."""
        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/oauth/configure",
                headers={"Authorization": "Bearer test"},
                json={"client_id": "test"},  # Missing required fields
            )

        assert response.status_code == 422


class TestOAuthRefresh:
    """Tests for POST /api/oauth/refresh endpoint."""

    @pytest.mark.asyncio
    async def test_refresh_no_credentials(self, oauth_app):
        """Test refresh fails when no credentials configured."""
        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/oauth/refresh",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 400
        assert "No OAuth credentials" in response.json()["detail"]
