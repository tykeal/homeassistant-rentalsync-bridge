# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for Cloudbeds service."""

import httpx
import pytest
from src.services import cloudbeds_service
from src.services.cloudbeds_service import (
    DEFAULT_LOOKBACK_DAYS,
    RESERVATIONS_PAGE_SIZE,
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
                    "propertyID": "PROP123",
                    "rooms": [
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
            "data": [{"propertyID": "EMPTY_PROP", "rooms": []}],
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
                    "data": [
                        {
                            "propertyID": "PROP123",
                            "rooms": [{"roomID": "123", "roomName": "Room 1"}],
                        }
                    ],
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


class TestGetReservationsDateWindow:
    """Tests for the default reservation fetch window."""

    @pytest.mark.asyncio
    async def test_default_start_date_includes_in_progress_stays(self, monkeypatch):
        """Test default checkInFrom reaches a year into the past."""
        from datetime import UTC, datetime, timedelta
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
        await service.get_reservations("PROP_ABC")

        params = captured_kwargs["params"]
        # Cloudbeds v1.3 getReservations documents checkInFrom/checkOutTo as
        # the stay-date bounds; startDate/endDate are not supported.
        assert "startDate" not in params
        assert "endDate" not in params
        start = datetime.strptime(params["checkInFrom"], "%Y-%m-%d").replace(tzinfo=UTC)
        end = datetime.strptime(params["checkOutTo"], "%Y-%m-%d").replace(tzinfo=UTC)
        now = datetime.now(UTC)

        assert DEFAULT_LOOKBACK_DAYS == 365
        assert start <= now - timedelta(days=DEFAULT_LOOKBACK_DAYS - 1)
        assert end >= now + timedelta(days=364)

    @pytest.mark.asyncio
    async def test_status_filter_includes_cancellations(self, monkeypatch):
        """Test cancellations are requested so they are not silently absent."""
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
        await service.get_reservations("PROP_ABC")

        statuses = captured_kwargs["params"]["status"].split(",")
        # Cloudbeds spells the cancelled status with a single "l"
        assert "canceled" in statuses
        assert "confirmed" in statuses
        assert "checked_in" in statuses
        assert "checked_out" in statuses


class TestGetReservationsPagination:
    """Tests for paginated reservation fetching."""

    @staticmethod
    def _install_paged_client(monkeypatch, pages):
        """Install a mock httpx client serving the supplied pages.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            pages: Mapping of page number to either a reservation list or an
                integer HTTP status code to respond with for that page.

        Returns:
            List capturing the requested page numbers, in order.
        """
        from unittest.mock import AsyncMock, MagicMock

        requested_pages = []

        async def capture_get(*_args, **kwargs):
            """Serve the configured page for the requested pageNumber."""
            page_number = int(kwargs["params"]["pageNumber"])
            requested_pages.append(page_number)
            page = pages[page_number]
            mock_response = MagicMock()
            if isinstance(page, int):
                mock_response.status_code = page
                mock_response.headers = {}
                mock_response.text = "server error"
                return mock_response
            mock_response.status_code = 200
            mock_response.json.return_value = {"success": True, "data": page}
            return mock_response

        mock_client = AsyncMock()
        mock_client.get = capture_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)
        return requested_pages

    @staticmethod
    def _make_page(start, count):
        """Build a page of synthetic reservations.

        Args:
            start: First reservation index on the page.
            count: Number of reservations on the page.

        Returns:
            List of reservation dictionaries.
        """
        return [{"reservationID": f"RES{start + i}"} for i in range(count)]

    @pytest.mark.asyncio
    async def test_multiple_pages_are_combined(self, monkeypatch):
        """Test full pages are followed until a short page ends the run."""
        pages = {
            1: self._make_page(0, RESERVATIONS_PAGE_SIZE),
            2: self._make_page(RESERVATIONS_PAGE_SIZE, RESERVATIONS_PAGE_SIZE),
            3: self._make_page(2 * RESERVATIONS_PAGE_SIZE, 7),
        }
        requested_pages = self._install_paged_client(monkeypatch, pages)

        service = CloudbedsService(access_token="test_token")
        result = await service.get_reservations("PROP_ABC")

        assert requested_pages == [1, 2, 3]
        assert len(result) == 2 * RESERVATIONS_PAGE_SIZE + 7
        assert result[0]["reservationID"] == "RES0"
        assert result[-1]["reservationID"] == f"RES{2 * RESERVATIONS_PAGE_SIZE + 6}"

    @pytest.mark.asyncio
    async def test_single_short_page_stops_immediately(self, monkeypatch):
        """Test a single short page terminates without a second request."""
        pages = {1: self._make_page(0, 3)}
        requested_pages = self._install_paged_client(monkeypatch, pages)

        service = CloudbedsService(access_token="test_token")
        result = await service.get_reservations("PROP_ABC")

        assert requested_pages == [1]
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_later_page_failure_returns_nothing(self, monkeypatch):
        """Test a failing later page raises without partial data."""
        pages = {
            1: self._make_page(0, RESERVATIONS_PAGE_SIZE),
            2: 500,
        }
        self._install_paged_client(monkeypatch, pages)

        service = CloudbedsService(access_token="test_token")
        with pytest.raises(CloudbedsServiceError):
            await service.get_reservations("PROP_ABC")

    @pytest.mark.asyncio
    async def test_page_size_param_is_sent(self, monkeypatch):
        """Test the documented maximum page size is requested explicitly."""
        from unittest.mock import AsyncMock, MagicMock

        captured_kwargs = {}

        async def capture_get(*_args, **kwargs):
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

        monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

        service = CloudbedsService(access_token="test_token")
        await service.get_reservations("PROP_ABC")

        assert captured_kwargs["params"]["pageSize"] == str(RESERVATIONS_PAGE_SIZE)
        assert captured_kwargs["params"]["pageNumber"] == "1"

    @pytest.mark.asyncio
    async def test_runaway_pagination_is_bounded(self, monkeypatch):
        """Test an API that never returns a short page raises an error."""
        from unittest.mock import AsyncMock, MagicMock

        async def capture_get(*_args, **_kwargs):
            """Always return a full page to simulate a misbehaving API."""
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "success": True,
                "data": self._make_page(0, RESERVATIONS_PAGE_SIZE),
            }
            return mock_response

        mock_client = AsyncMock()
        mock_client.get = capture_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)
        monkeypatch.setattr(cloudbeds_service, "MAX_RESERVATION_PAGES", 3)

        service = CloudbedsService(access_token="test_token")
        with pytest.raises(CloudbedsServiceError, match="exceeded"):
            await service.get_reservations("PROP_ABC")

    @pytest.mark.asyncio
    async def test_rate_limit_on_later_page_is_retried(self, monkeypatch):
        """Test HTTP 429 handling still applies to pages after the first."""
        from unittest.mock import AsyncMock, MagicMock

        calls = []

        async def capture_get(*_args, **kwargs):
            """Rate limit page 2 once, then serve it normally."""
            page_number = int(kwargs["params"]["pageNumber"])
            calls.append(page_number)
            mock_response = MagicMock()
            if page_number == 2 and calls.count(2) == 1:
                mock_response.status_code = 429
                mock_response.headers = {"Retry-After": "0"}
                return mock_response
            mock_response.status_code = 200
            data = (
                self._make_page(0, RESERVATIONS_PAGE_SIZE)
                if page_number == 1
                else self._make_page(RESERVATIONS_PAGE_SIZE, 1)
            )
            mock_response.json.return_value = {"success": True, "data": data}
            return mock_response

        mock_client = AsyncMock()
        mock_client.get = capture_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

        service = CloudbedsService(access_token="test_token")
        result = await service.get_reservations("PROP_ABC")

        assert calls == [1, 2, 2]
        assert len(result) == RESERVATIONS_PAGE_SIZE + 1
