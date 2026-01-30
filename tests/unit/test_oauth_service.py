# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for OAuth service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from src.services.oauth_service import (
    TOKEN_EXPIRY_BUFFER_SECONDS,
    OAuthService,
    OAuthServiceError,
)


class TestOAuthService:
    """Tests for OAuthService."""

    @pytest.fixture
    def mock_session(self):
        """Create mock database session."""
        session = AsyncMock()
        return session

    @pytest.fixture
    def mock_credential(self):
        """Create mock OAuth credential."""
        cred = MagicMock()
        cred.access_token = "old_access_token"
        cred.refresh_token = "test_refresh_token"
        cred.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
        cred.is_token_expired.return_value = False
        return cred

    @pytest.fixture
    def service(self, mock_session):
        """Create OAuth service."""
        return OAuthService(mock_session)

    @pytest.mark.asyncio
    async def test_refresh_token_success(self, service, mock_credential):
        """Test successful token refresh."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
        }

        with patch("src.services.oauth_service.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            access, refresh, expires = await service.refresh_token(mock_credential)

        assert access == "new_access_token"
        assert refresh == "new_refresh_token"
        assert expires > datetime.now(UTC)

    @pytest.mark.asyncio
    async def test_refresh_token_no_refresh_token(self, service):
        """Test refresh fails without refresh token."""
        cred = MagicMock()
        cred.refresh_token = None

        with pytest.raises(OAuthServiceError, match="No refresh token"):
            await service.refresh_token(cred)

    @pytest.mark.asyncio
    async def test_refresh_token_api_error(self, service, mock_credential):
        """Test refresh fails on API error."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid token"

        with patch("src.services.oauth_service.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            with pytest.raises(OAuthServiceError, match="Token refresh failed"):
                await service.refresh_token(mock_credential)

    @pytest.mark.asyncio
    async def test_refresh_token_network_error(self, service, mock_credential):
        """Test refresh fails on network error."""
        with patch("src.services.oauth_service.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=httpx.RequestError("Network error")
            )
            with pytest.raises(OAuthServiceError, match="HTTP error"):
                await service.refresh_token(mock_credential)

    @pytest.mark.asyncio
    async def test_refresh_and_save(self, service, mock_session, mock_credential):
        """Test refresh and save updates credential."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "saved_access_token",
            "refresh_token": "saved_refresh_token",
            "expires_in": 3600,
        }

        with patch("src.services.oauth_service.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            result = await service.refresh_and_save(mock_credential)

        assert result.access_token == "saved_access_token"
        assert result.refresh_token == "saved_refresh_token"
        mock_session.commit.assert_awaited_once()


class TestShouldRefresh:
    """Tests for should_refresh method."""

    @pytest.fixture
    def service(self):
        """Create OAuth service."""
        return OAuthService(AsyncMock())

    def test_should_refresh_expired(self, service):
        """Test should refresh when token is expired."""
        cred = MagicMock()
        cred.is_token_expired.return_value = True

        assert service.should_refresh(cred) is True

    def test_should_refresh_within_buffer(self, service):
        """Test should refresh when within buffer period."""
        cred = MagicMock()
        cred.is_token_expired.return_value = False
        # Expires in 2 minutes (within 5 minute buffer)
        cred.token_expires_at = datetime.now(UTC) + timedelta(minutes=2)

        assert service.should_refresh(cred) is True

    def test_should_not_refresh_fresh(self, service):
        """Test should not refresh when token is fresh."""
        cred = MagicMock()
        cred.is_token_expired.return_value = False
        # Expires in 10 minutes (outside buffer)
        cred.token_expires_at = datetime.now(UTC) + timedelta(
            seconds=TOKEN_EXPIRY_BUFFER_SECONDS + 60
        )

        assert service.should_refresh(cred) is False

    def test_should_refresh_no_expiry(self, service):
        """Test should not refresh when no expiry set."""
        cred = MagicMock()
        cred.is_token_expired.return_value = False
        cred.token_expires_at = None

        assert service.should_refresh(cred) is False


class TestParseTokenResponse:
    """Tests for token response parsing."""

    @pytest.fixture
    def service(self):
        """Create OAuth service."""
        return OAuthService(AsyncMock())

    def test_parse_valid_response(self, service):
        """Test parsing valid token response."""
        data = {
            "access_token": "test_access",
            "refresh_token": "test_refresh",
            "expires_in": 7200,
        }

        access, refresh, expires = service._parse_token_response(data)

        assert access == "test_access"
        assert refresh == "test_refresh"
        # Expires should be about 2 hours from now
        assert expires > datetime.now(UTC) + timedelta(hours=1, minutes=59)

    def test_parse_missing_access_token(self, service):
        """Test parsing fails without access_token."""
        data = {"refresh_token": "test"}

        with pytest.raises(OAuthServiceError, match="No access_token"):
            service._parse_token_response(data)

    def test_parse_missing_refresh_token(self, service):
        """Test parsing fails without refresh_token."""
        data = {"access_token": "test"}

        with pytest.raises(OAuthServiceError, match="No refresh_token"):
            service._parse_token_response(data)

    def test_parse_default_expires_in(self, service):
        """Test default expires_in is used when not provided."""
        data = {
            "access_token": "test_access",
            "refresh_token": "test_refresh",
        }

        _access, _refresh, expires = service._parse_token_response(data)

        # Default is 3600 seconds = 1 hour
        assert expires > datetime.now(UTC) + timedelta(minutes=59)
        assert expires < datetime.now(UTC) + timedelta(minutes=61)
