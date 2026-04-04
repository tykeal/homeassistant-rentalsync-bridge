# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for GuestyTokenManager."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from src.providers.base import (
    PMSAuthenticationError,
    PMSConnectionError,
    TokenRateLimitError,
)
from src.providers.guesty.auth import (
    GUESTY_TOKEN_URL,
    TOKEN_REQUEST_LIMIT,
    TOKEN_WARN_THRESHOLD,
    GuestyTokenManager,
)
from src.repositories.credential_repository import TOKEN_REQUEST_WINDOW

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_repo():
    """Create a mock CredentialRepository with default return values."""
    repo = AsyncMock()
    repo.get_token_request_count = AsyncMock(return_value=0)
    repo.increment_token_request_count = AsyncMock()
    repo.update_token = AsyncMock()

    mock_credential = MagicMock()
    mock_credential.token_request_window_start = None
    mock_credential.access_token = None
    mock_credential.token_expires_at = None
    repo.get_credential = AsyncMock(return_value=mock_credential)
    return repo


@pytest.fixture
def token_manager(mock_repo):
    """Create a GuestyTokenManager with test credentials."""
    return GuestyTokenManager(
        client_id="test-client-id",
        client_secret="test-client-secret",
        credential_repo=mock_repo,
        credential_id=1,
    )


def _make_token_response(
    access_token="test-access-token",
    expires_in=86400,
    status_code=200,
):
    """Build a mock httpx.Response for the token endpoint."""
    return httpx.Response(
        status_code=status_code,
        json={"access_token": access_token, "expires_in": expires_in},
        request=httpx.Request("POST", GUESTY_TOKEN_URL),
    )


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


class TestTokenCache:
    """Tests for GuestyTokenManager token caching."""

    @pytest.mark.asyncio
    async def test_cache_miss_requests_new_token(self, token_manager, mock_repo):
        """Test that a cache miss triggers a new token request."""
        with patch.object(
            token_manager,
            "_request_token",
            new_callable=AsyncMock,
        ) as mock_request:
            from src.providers.base import TokenResult

            mock_request.return_value = TokenResult(
                access_token="fresh-token",
                refresh_token=None,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )

            token = await token_manager.get_token()

            assert token == "fresh-token"
            mock_request.assert_awaited_once()
            mock_repo.update_token.assert_awaited_once()
            mock_repo.increment_token_request_count.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_token(self, token_manager, mock_repo):
        """Test that a valid cached token is returned without API call."""
        token_manager._cached_token = "cached-token"
        token_manager._cached_expires_at = datetime.now(UTC) + timedelta(hours=12)

        with patch.object(
            token_manager,
            "_request_token",
            new_callable=AsyncMock,
        ) as mock_request:
            token = await token_manager.get_token()

            assert token == "cached-token"
            mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_cache_requests_new_token(self, token_manager, mock_repo):
        """Test that an expired cached token triggers a new request."""
        token_manager._cached_token = "old-token"
        token_manager._cached_expires_at = datetime.now(UTC) - timedelta(hours=1)

        with patch.object(
            token_manager,
            "_request_token",
            new_callable=AsyncMock,
        ) as mock_request:
            from src.providers.base import TokenResult

            mock_request.return_value = TokenResult(
                access_token="new-token",
                refresh_token=None,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )

            token = await token_manager.get_token()

            assert token == "new-token"
            mock_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalidate_cache_clears_state(self, token_manager, mock_repo):
        """Test that invalidate_cache clears token and expiry and DB."""
        token_manager._cached_token = "some-token"
        token_manager._cached_expires_at = datetime.now(UTC) + timedelta(hours=12)

        await token_manager.invalidate_cache()

        assert token_manager.cached_token is None
        assert token_manager.cached_expires_at is None
        mock_repo.update_token.assert_awaited_once_with(1, None, None)


# ---------------------------------------------------------------------------
# Rate tracking tests
# ---------------------------------------------------------------------------


class TestRateTracking:
    """Tests for GuestyTokenManager rate limit tracking."""

    @pytest.mark.asyncio
    async def test_allows_requests_under_threshold(self, token_manager, mock_repo):
        """Test that requests below the warning threshold are allowed."""
        mock_repo.get_token_request_count.return_value = 2

        with patch.object(
            token_manager,
            "_request_token",
            new_callable=AsyncMock,
        ) as mock_request:
            from src.providers.base import TokenResult

            mock_request.return_value = TokenResult(
                access_token="tok",
                refresh_token=None,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )

            token = await token_manager.get_token()
            assert token == "tok"

    @pytest.mark.asyncio
    async def test_warns_at_4th_request(self, token_manager, mock_repo, caplog):
        """Test that a warning is logged at the 4th request."""
        mock_repo.get_token_request_count.return_value = TOKEN_WARN_THRESHOLD

        with patch.object(
            token_manager,
            "_request_token",
            new_callable=AsyncMock,
        ) as mock_request:
            from src.providers.base import TokenResult

            mock_request.return_value = TokenResult(
                access_token="tok4",
                refresh_token=None,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )

            import logging

            with caplog.at_level(logging.WARNING):
                token = await token_manager.get_token()

            assert token == "tok4"
            assert "rate limit warning" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_allows_5th_with_warning(self, token_manager, mock_repo, caplog):
        """Test that the 5th request is allowed with a warning."""
        mock_repo.get_token_request_count.return_value = TOKEN_REQUEST_LIMIT - 1

        with patch.object(
            token_manager,
            "_request_token",
            new_callable=AsyncMock,
        ) as mock_request:
            from src.providers.base import TokenResult

            mock_request.return_value = TokenResult(
                access_token="tok5",
                refresh_token=None,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )

            import logging

            with caplog.at_level(logging.WARNING):
                token = await token_manager.get_token()

            assert token == "tok5"
            assert "rate limit warning" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_defers_at_6th_request(self, token_manager, mock_repo):
        """Test that the 6th request raises TokenRateLimitError."""
        mock_repo.get_token_request_count.return_value = TOKEN_REQUEST_LIMIT

        with pytest.raises(TokenRateLimitError, match="rate limit exceeded"):
            await token_manager.get_token()


# ---------------------------------------------------------------------------
# Window reset tests
# ---------------------------------------------------------------------------


class TestWindowReset:
    """Tests for rate limit window reset behaviour."""

    @pytest.mark.asyncio
    async def test_reset_at_included_in_error(self, token_manager, mock_repo):
        """Test that reset_at is computed from window_start + 24h."""
        mock_repo.get_token_request_count.return_value = TOKEN_REQUEST_LIMIT

        window_start = datetime.now(UTC) - timedelta(hours=12)
        mock_credential = MagicMock()
        mock_credential.token_request_window_start = window_start
        mock_credential.access_token = None
        mock_credential.token_expires_at = None
        mock_repo.get_credential.return_value = mock_credential

        with pytest.raises(TokenRateLimitError) as exc_info:
            await token_manager.get_token()

        assert exc_info.value.reset_at is not None
        expected_reset = window_start + timedelta(hours=24)
        assert abs((exc_info.value.reset_at - expected_reset).total_seconds()) < 1

    @pytest.mark.asyncio
    async def test_none_window_gives_none_reset(self, token_manager, mock_repo):
        """Test that reset_at is None when no window has started."""
        mock_repo.get_token_request_count.return_value = TOKEN_REQUEST_LIMIT

        mock_credential = MagicMock()
        mock_credential.token_request_window_start = None
        mock_credential.access_token = None
        mock_credential.token_expires_at = None
        mock_repo.get_credential.return_value = mock_credential

        with pytest.raises(TokenRateLimitError) as exc_info:
            await token_manager.get_token()

        assert exc_info.value.reset_at is None

    @pytest.mark.asyncio
    async def test_expired_window_resets_rate_limit(self, token_manager, mock_repo):
        """Token request allowed when window has expired even at limit."""
        mock_repo.get_token_request_count.return_value = TOKEN_REQUEST_LIMIT

        # Window started more than 24h ago → expired
        window_start = datetime.now(UTC) - TOKEN_REQUEST_WINDOW - timedelta(hours=1)
        mock_credential = MagicMock()
        mock_credential.token_request_window_start = window_start
        mock_credential.access_token = None
        mock_credential.token_expires_at = None
        mock_repo.get_credential.return_value = mock_credential

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=_make_token_response())
        token_manager._http_client = mock_http

        # Should NOT raise TokenRateLimitError
        token = await token_manager.get_token()
        assert token == "test-access-token"


# ---------------------------------------------------------------------------
# Token request tests
# ---------------------------------------------------------------------------


class TestTokenRequest:
    """Tests for the underlying token HTTP request."""

    @pytest.mark.asyncio
    async def test_successful_token_request(self, mock_repo):
        """Test that a successful token request returns a TokenResult."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(return_value=_make_token_response())

        mgr = GuestyTokenManager(
            client_id="cid",
            client_secret="csecret",
            credential_repo=mock_repo,
            credential_id=1,
            http_client=mock_http,
        )

        result = await mgr._request_token()

        assert result.access_token == "test-access-token"
        assert result.refresh_token is None
        mock_http.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self, mock_repo):
        """Test that a 401 response raises PMSAuthenticationError."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(
            return_value=httpx.Response(
                status_code=401,
                text="Unauthorized",
                request=httpx.Request("POST", GUESTY_TOKEN_URL),
            )
        )

        mgr = GuestyTokenManager(
            client_id="cid",
            client_secret="bad",
            credential_repo=mock_repo,
            credential_id=1,
            http_client=mock_http,
        )

        with pytest.raises(PMSAuthenticationError, match="401"):
            await mgr._request_token()

    @pytest.mark.asyncio
    async def test_network_error_raises_connection_error(self, mock_repo):
        """Test that a network error raises PMSConnectionError."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(side_effect=httpx.ConnectError("DNS fail"))

        mgr = GuestyTokenManager(
            client_id="cid",
            client_secret="csecret",
            credential_repo=mock_repo,
            credential_id=1,
            http_client=mock_http,
        )

        with pytest.raises(PMSConnectionError, match="DNS fail"):
            await mgr._request_token()

    @pytest.mark.asyncio
    async def test_500_raises_auth_error(self, mock_repo):
        """Test that a 500 response raises PMSAuthenticationError."""
        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.post = AsyncMock(
            return_value=httpx.Response(
                status_code=500,
                text="Internal Server Error",
                request=httpx.Request("POST", GUESTY_TOKEN_URL),
            )
        )

        mgr = GuestyTokenManager(
            client_id="cid",
            client_secret="csecret",
            credential_repo=mock_repo,
            credential_id=1,
            http_client=mock_http,
        )

        with pytest.raises(PMSAuthenticationError, match="500"):
            await mgr._request_token()
