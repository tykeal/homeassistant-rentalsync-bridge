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
from src.repositories.custom_field_repository import BUILTIN_FIELDS


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
    async def test_get_available_custom_fields_returns_builtin_fields(
        self, mock_db_session: AsyncMock, mock_listing: Listing
    ) -> None:
        """Test endpoint returns built-in custom fields when no discovered fields."""
        # Mock the listing repository to return our test listing (first execute)
        # Mock the available_fields query to return empty (second execute)
        mock_listing_result = MagicMock()
        mock_listing_result.scalar_one_or_none.return_value = mock_listing

        mock_available_result = MagicMock()
        mock_available_result.scalars.return_value.all.return_value = []

        mock_db_session.execute.side_effect = [
            mock_listing_result,
            mock_available_result,
        ]

        # Call the endpoint
        response = await get_available_custom_fields(
            listing_id=1,
            db=mock_db_session,
        )

        # Verify response structure
        assert "available_fields" in response
        assert "listing_id" in response
        assert response["listing_id"] == 1

        # Verify built-in fields are included
        available = response["available_fields"]
        assert isinstance(available, list)
        # At minimum, built-in fields should be present
        assert len(available) >= len(BUILTIN_FIELDS)

        # Verify guest_phone_last4 (built-in) is in the response
        field_keys = [f["field_key"] for f in available]
        assert "guest_phone_last4" in field_keys

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
    async def test_get_available_custom_fields_returns_proper_format(
        self, mock_db_session: AsyncMock, mock_listing: Listing
    ) -> None:
        """Test endpoint returns fields in proper format (list of objects)."""
        # Mock the listing repository
        mock_listing_result = MagicMock()
        mock_listing_result.scalar_one_or_none.return_value = mock_listing

        mock_available_result = MagicMock()
        mock_available_result.scalars.return_value.all.return_value = []

        mock_db_session.execute.side_effect = [
            mock_listing_result,
            mock_available_result,
        ]

        # Call the endpoint
        response = await get_available_custom_fields(
            listing_id=1,
            db=mock_db_session,
        )

        available = response["available_fields"]

        # Should include default Cloudbeds fields + built-in fields
        assert len(available) > 0

        # Verify each entry has expected keys
        for field in available:
            assert "field_key" in field
            assert "display_name" in field
            assert "sample_value" in field
            assert "source" in field
            assert isinstance(field["field_key"], str)
            assert isinstance(field["display_name"], str)
            assert len(field["field_key"]) > 0
            assert len(field["display_name"]) > 0
