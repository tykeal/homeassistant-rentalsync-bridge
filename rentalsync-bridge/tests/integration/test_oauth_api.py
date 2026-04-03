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
        assert data["pms_type"] is not None

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
        assert data["pms_type"] == "cloudbeds"
        assert data["token_requests_remaining"] is None

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

    @pytest.mark.asyncio
    async def test_status_guesty_credential(
        self, oauth_app, oauth_session, monkeypatch
    ):
        """Test status returns token_requests_remaining for guesty."""
        monkeypatch.setenv("PMS_TYPE", "guesty")
        from src.config import get_settings

        get_settings.cache_clear()

        cred = OAuthCredential(client_id="gu_client", pms_type="guesty")
        cred.client_secret = "secret"
        cred.access_token = "access"
        cred.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        cred.token_request_count = 2
        cred.token_request_window_start = datetime.now(UTC) - timedelta(hours=1)
        oauth_session.add(cred)
        await oauth_session.commit()

        try:
            async with AsyncClient(
                transport=ASGITransport(app=oauth_app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/oauth/status",
                    headers={"Authorization": "Bearer test"},
                )
        finally:
            get_settings.cache_clear()

        assert response.status_code == 200
        data = response.json()
        assert data["pms_type"] == "guesty"
        assert data["token_requests_remaining"] == 3


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

    @pytest.mark.asyncio
    async def test_configure_guesty_rejects_api_key(self, oauth_app):
        """Test Guesty rejects api_key authentication."""
        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/oauth/configure",
                headers={"Authorization": "Bearer test"},
                json={
                    "pms_type": "guesty",
                    "client_id": "gu_client",
                    "client_secret": "gu_secret",
                    "api_key": "should_fail",
                },
            )

        assert response.status_code == 400
        assert "API key" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_configure_guesty_rejects_refresh_token(self, oauth_app):
        """Test Guesty rejects refresh_token."""
        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/oauth/configure",
                headers={"Authorization": "Bearer test"},
                json={
                    "pms_type": "guesty",
                    "client_id": "gu_client",
                    "client_secret": "gu_secret",
                    "refresh_token": "should_fail",
                },
            )

        assert response.status_code == 400
        assert "refresh token" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_configure_guesty_success(self, oauth_app):
        """Test Guesty configure with only client credentials."""
        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/oauth/configure",
                headers={"Authorization": "Bearer test"},
                json={
                    "pms_type": "guesty",
                    "client_id": "gu_client",
                    "client_secret": "gu_secret",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_configure_cloudbeds_requires_token_or_key(self, oauth_app):
        """Cloudbeds needs api_key or access_token."""
        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/oauth/configure",
                headers={"Authorization": "Bearer test"},
                json={
                    "pms_type": "cloudbeds",
                    "client_id": "cb_client",
                    "client_secret": "cb_secret",
                },
            )

        assert response.status_code == 400
        assert "api_key or access_token" in response.json()["detail"]


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


class TestProvidersEndpoint:
    """Tests for GET /api/providers endpoint."""

    @pytest.mark.asyncio
    async def test_providers_returns_list(self, oauth_app):
        """GET /api/providers returns at least the cloudbeds provider."""
        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/providers",
                headers={"Authorization": "Bearer test"},
            )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        pms_types = {p["pms_type"] for p in data}
        assert "cloudbeds" in pms_types

    @pytest.mark.asyncio
    async def test_providers_has_credential_fields(self, oauth_app):
        """Each provider entry contains credential_fields and registered."""
        async with AsyncClient(
            transport=ASGITransport(app=oauth_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/providers",
                headers={"Authorization": "Bearer test"},
            )

        data = response.json()
        for entry in data:
            assert "credential_fields" in entry
            assert isinstance(entry["credential_fields"], list)
            assert "registered" in entry
            assert isinstance(entry["registered"], bool)
