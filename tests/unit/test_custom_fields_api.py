# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for custom fields API endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

# mypy: disable-error-code="attr-defined"
from src.api.custom_fields import get_available_custom_fields
from src.models.listing import Listing
from src.repositories.custom_field_repository import AVAILABLE_FIELDS


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """Create a mock database session."""
    session = AsyncMock(spec=AsyncSession)
    return session


@pytest.fixture
def mock_listing() -> Listing:
    """Create a mock listing."""
    return Listing(
        id=1,
        cloudbeds_id="PROP123",
        name="Test Property",
        ical_url_slug="test-property",
        enabled=True,
        sync_enabled=True,
        timezone="America/Los_Angeles",
    )


class TestGetAvailableCustomFields:
    """Tests for GET /api/listings/{id}/available-custom-fields endpoint."""

    @pytest.mark.asyncio
    async def test_get_available_custom_fields_returns_all_fields(
        self, mock_db_session: AsyncMock, mock_listing: Listing
    ) -> None:
        """Test endpoint returns all available custom fields."""
        # Mock the listing repository to return our test listing
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_listing
        mock_db_session.execute.return_value = mock_result

        # Call the endpoint
        response = await get_available_custom_fields(
            listing_id=1,
            db=mock_db_session,
        )

        # Verify response structure
        assert "available_fields" in response
        assert "listing_id" in response
        assert response["listing_id"] == 1

        # Verify all AVAILABLE_FIELDS are included
        available = response["available_fields"]
        assert isinstance(available, dict)
        assert len(available) == len(AVAILABLE_FIELDS)

        # Verify guest_phone_last4 is in the response
        assert "guest_phone_last4" in available
        assert available["guest_phone_last4"] == "Guest Phone (Last 4 Digits)"

    @pytest.mark.asyncio
    async def test_get_available_custom_fields_listing_not_found(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Test endpoint raises 404 when listing not found."""
        # Mock the listing repository to return None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        # Call should raise HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_available_custom_fields(
                listing_id=999,
                db=mock_db_session,
            )

        assert exc_info.value.status_code == 404
        assert "not found" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_get_available_custom_fields_includes_all_standard_fields(
        self, mock_db_session: AsyncMock, mock_listing: Listing
    ) -> None:
        """Test endpoint includes all standard fields from AVAILABLE_FIELDS."""
        # Mock the listing repository
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_listing
        mock_db_session.execute.return_value = mock_result

        # Call the endpoint
        response = await get_available_custom_fields(
            listing_id=1,
            db=mock_db_session,
        )

        available = response["available_fields"]

        # Verify standard fields are present
        expected_fields = [
            "booking_notes",
            "arrival_time",
            "departure_time",
            "num_guests",
            "room_type_name",
            "source_name",
            "special_requests",
            "estimated_arrival",
            "guest_phone_last4",  # New field
        ]

        for field_name in expected_fields:
            assert field_name in available, (
                f"Expected field {field_name} not in response"
            )

    @pytest.mark.asyncio
    async def test_get_available_custom_fields_returns_proper_format(
        self, mock_db_session: AsyncMock, mock_listing: Listing
    ) -> None:
        """Test endpoint returns fields in proper format (dict of name: label)."""
        # Mock the listing repository
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_listing
        mock_db_session.execute.return_value = mock_result

        # Call the endpoint
        response = await get_available_custom_fields(
            listing_id=1,
            db=mock_db_session,
        )

        available = response["available_fields"]

        # Verify each entry has a string key and string value
        for field_name, display_label in available.items():
            assert isinstance(field_name, str)
            assert isinstance(display_label, str)
            assert len(field_name) > 0
            assert len(display_label) > 0
