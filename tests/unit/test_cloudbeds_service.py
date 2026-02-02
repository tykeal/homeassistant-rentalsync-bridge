# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for Cloudbeds service."""

import pytest
from src.services.cloudbeds_service import (
    CloudbedsService,
    CloudbedsServiceError,
    RateLimitError,
)


class TestPhoneExtraction:
    """Tests for phone number extraction."""

    def test_extract_phone_last4_valid(self):
        """Test extracting last 4 digits from valid phone."""
        assert CloudbedsService.extract_phone_last4("555-123-4567") == "4567"

    def test_extract_phone_last4_no_dashes(self):
        """Test extracting from phone without dashes."""
        assert CloudbedsService.extract_phone_last4("5551234567") == "4567"

    def test_extract_phone_last4_short(self):
        """Test with phone number too short."""
        assert CloudbedsService.extract_phone_last4("123") is None

    def test_extract_phone_last4_none(self):
        """Test with None input."""
        assert CloudbedsService.extract_phone_last4(None) is None

    def test_extract_phone_last4_empty(self):
        """Test with empty string."""
        assert CloudbedsService.extract_phone_last4("") is None


class TestRateLimitHandling:
    """Tests for rate limit handling with exponential backoff (T069)."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, monkeypatch):
        """Test that retry succeeds after initial rate limit."""
        call_count = 0

        async def mock_operation():
            """Mock operation that fails first then succeeds."""
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError("Rate limited", retry_after=0.01)
            return "success"

        # Reduce delays for fast test
        monkeypatch.setattr("src.services.cloudbeds_service.BASE_DELAY_SECONDS", 0.01)

        service = CloudbedsService(access_token="test")
        result = await service._with_retry("test_op", mock_operation)

        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_error(self, monkeypatch):
        """Test that error is raised after max retries."""
        call_count = 0

        async def mock_operation():
            """Mock operation that always fails."""
            nonlocal call_count
            call_count += 1
            raise RateLimitError("Rate limited", retry_after=0.01)

        # Reduce delays for fast test
        monkeypatch.setattr("src.services.cloudbeds_service.BASE_DELAY_SECONDS", 0.01)
        monkeypatch.setattr("src.services.cloudbeds_service.MAX_RETRIES", 2)

        service = CloudbedsService(access_token="test")
        with pytest.raises(CloudbedsServiceError) as exc_info:
            await service._with_retry("test_op", mock_operation)

        assert "failed after" in str(exc_info.value)
        assert call_count == 3  # Initial + 2 retries

    @pytest.mark.asyncio
    async def test_uses_retry_after_header(self, monkeypatch):
        """Test that retry_after from API is respected."""
        import time

        call_count = 0
        call_times = []

        async def mock_operation():
            """Mock operation that tracks call timing."""
            nonlocal call_count
            call_count += 1
            call_times.append(time.time())
            if call_count == 1:
                raise RateLimitError("Rate limited", retry_after=0.05)
            return "success"

        monkeypatch.setattr("src.services.cloudbeds_service.BASE_DELAY_SECONDS", 0.01)

        service = CloudbedsService(access_token="test")
        await service._with_retry("test_op", mock_operation)

        # Should have waited approximately 0.05 seconds
        assert len(call_times) == 2
        delay = call_times[1] - call_times[0]
        assert delay >= 0.04  # Allow some tolerance

    def test_rate_limit_error_stores_retry_after(self):
        """Test RateLimitError stores retry_after value."""
        error = RateLimitError("Rate limited", retry_after=30.0)
        assert error.retry_after == 30.0
        assert str(error) == "Rate limited"

    def test_rate_limit_error_default_retry_after(self):
        """Test RateLimitError with no retry_after."""
        error = RateLimitError("Rate limited")
        assert error.retry_after is None


class TestGetRooms:
    """Tests for get_rooms() method (T016)."""

    @pytest.mark.asyncio
    async def test_get_rooms_returns_room_list(self, monkeypatch):
        """Test get_rooms returns list of rooms for a property."""
        from unittest.mock import AsyncMock, MagicMock

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": [
                {
                    "roomID": "123",
                    "roomName": "Room 101",
                    "roomTypeName": "Standard Room",
                },
                {
                    "roomID": "456",
                    "roomName": "Room 102",
                    "roomTypeName": "Deluxe Suite",
                },
            ],
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

        service = CloudbedsService(access_token="test_token")
        rooms = await service.get_rooms("PROP123")

        assert len(rooms) == 2
        assert rooms[0]["roomID"] == "123"
        assert rooms[0]["roomName"] == "Room 101"
        assert rooms[0]["roomTypeName"] == "Standard Room"
        assert rooms[1]["roomID"] == "456"

    @pytest.mark.asyncio
    async def test_get_rooms_empty_property(self, monkeypatch):
        """Test get_rooms returns empty list for property with no rooms."""
        from unittest.mock import AsyncMock, MagicMock

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": [],
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

        service = CloudbedsService(access_token="test_token")
        rooms = await service.get_rooms("EMPTY_PROP")

        assert rooms == []

    @pytest.mark.asyncio
    async def test_get_rooms_handles_rate_limit(self, monkeypatch):
        """Test get_rooms handles rate limiting with retry."""
        from unittest.mock import AsyncMock, MagicMock

        call_count = 0

        async def mock_get(*args, **kwargs):
            """Mock GET that returns 429 first then success."""
            nonlocal call_count
            call_count += 1

            mock_response = MagicMock()
            if call_count == 1:
                mock_response.status_code = 429
                mock_response.headers = {"Retry-After": "0.01"}
            else:
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "success": True,
                    "data": [{"roomID": "123", "roomName": "Room 1"}],
                }
            return mock_response

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)
        monkeypatch.setattr("src.services.cloudbeds_service.BASE_DELAY_SECONDS", 0.01)

        service = CloudbedsService(access_token="test_token")
        rooms = await service.get_rooms("PROP123")

        assert len(rooms) == 1
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_get_rooms_api_error(self, monkeypatch):
        """Test get_rooms raises error on API failure."""
        from unittest.mock import AsyncMock, MagicMock

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

        service = CloudbedsService(access_token="test_token")
        with pytest.raises(CloudbedsServiceError) as exc_info:
            await service.get_rooms("PROP123")

        assert "API error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_rooms_sends_correct_params(self, monkeypatch):
        """Test get_rooms sends property_id in request."""
        from unittest.mock import AsyncMock, MagicMock

        captured_kwargs = {}

        async def capture_get(*args, **kwargs):
            """Capture GET kwargs for assertion."""
            captured_kwargs.update(kwargs)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"success": True, "data": []}
            return mock_response

        mock_client = AsyncMock()
        mock_client.get = capture_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

        service = CloudbedsService(access_token="test_token")
        await service.get_rooms("PROP_ABC")

        assert "params" in captured_kwargs
        assert captured_kwargs["params"]["propertyIDs"] == "PROP_ABC"
