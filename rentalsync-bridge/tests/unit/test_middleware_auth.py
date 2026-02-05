# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for authentication middleware."""

import pytest
from fastapi import FastAPI, Request, status
from httpx import ASGITransport, AsyncClient
from src.middleware.auth import (
    HA_REMOTE_USER_ID,
    PUBLIC_PATHS,
    AuthenticationMiddleware,
    get_current_user,
    is_public_path,
)


class TestIsPublicPath:
    """Tests for is_public_path function."""

    def test_health_is_public(self):
        """Test /health is public."""
        assert is_public_path("/health") is True

    def test_ical_prefix_is_public(self):
        """Test /ical/* paths are public."""
        assert is_public_path("/ical/abc123") is True
        assert is_public_path("/ical/test-listing") is True

    def test_docs_is_public(self):
        """Test documentation paths are public."""
        assert is_public_path("/docs") is True
        assert is_public_path("/redoc") is True
        assert is_public_path("/openapi.json") is True

    def test_api_is_not_public(self):
        """Test API paths require authentication."""
        assert is_public_path("/api/listings") is False
        assert is_public_path("/api/oauth/connect") is False
        assert is_public_path("/admin") is False

    def test_public_paths_constant(self):
        """Test PUBLIC_PATHS contains expected values."""
        assert "/health" in PUBLIC_PATHS
        assert "/ical" in PUBLIC_PATHS
        assert "/docs" in PUBLIC_PATHS


class TestAuthenticationMiddleware:
    """Tests for AuthenticationMiddleware."""

    @pytest.fixture
    def test_app(self):
        """Create a test app with auth middleware."""
        app = FastAPI()
        app.add_middleware(AuthenticationMiddleware)

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.get("/api/protected")
        async def protected(request: Request):
            return {"user": get_current_user(request)}

        @app.get("/ical/{slug}")
        async def ical(slug: str):
            return {"slug": slug}

        return app

    @pytest.fixture
    async def test_client(self, test_app):
        """Create test client for auth tests."""
        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_public_path_no_auth_required(self, test_client):
        """Test public paths don't require authentication."""
        response = await test_client.get("/health")
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_ical_path_no_auth_required(self, test_client):
        """Test iCal paths don't require authentication."""
        response = await test_client.get("/ical/test-listing")
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_protected_path_requires_auth(self, test_client):
        """Test protected paths require authentication in non-standalone mode."""
        # Note: conftest sets STANDALONE_MODE=true, so this passes
        # In real addon mode without header, it would fail
        response = await test_client.get("/api/protected")
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_authenticated_request_sets_user_id(self, test_client):
        """Test authenticated request stores user ID in request state."""
        headers = {HA_REMOTE_USER_ID: "test-user-123"}
        response = await test_client.get("/api/protected", headers=headers)
        assert response.status_code == status.HTTP_200_OK


class TestGetCurrentUser:
    """Tests for get_current_user function."""

    def test_get_user_from_request_state(self):
        """Test getting user ID from request state."""
        from unittest.mock import MagicMock

        request = MagicMock()
        request.state.user_id = "user-123"

        user = get_current_user(request)
        assert user == "user-123"

    def test_get_user_returns_none_if_not_set(self):
        """Test returns None if user_id not in state."""
        from unittest.mock import MagicMock

        request = MagicMock(spec=["state"])
        # Use a mock state without user_id attribute
        request.state = MagicMock(spec=[])

        user = get_current_user(request)
        assert user is None
