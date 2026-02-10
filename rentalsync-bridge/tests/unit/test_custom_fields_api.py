# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for custom fields API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

# mypy: disable-error-code="attr-defined"
from src.api.custom_fields import get_available_custom_fields
from src.models.listing import Listing


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
    @patch("src.api.custom_fields.AvailableFieldRepository")
    @patch("src.api.custom_fields.ListingRepository")
    async def test_get_available_custom_fields_returns_builtin_fields(
        self,
        mock_listing_repo_cls: MagicMock,
        mock_available_repo_cls: MagicMock,
        mock_db_session: AsyncMock,
        mock_listing: Listing,
    ) -> None:
        """Test endpoint returns built-in custom fields when no discovered fields."""
        # Mock ListingRepository.get_by_id to return listing
        mock_listing_repo = MagicMock()
        mock_listing_repo.get_by_id = AsyncMock(return_value=mock_listing)
        mock_listing_repo_cls.return_value = mock_listing_repo

        # Mock AvailableFieldRepository.get_enriched_available_fields
        mock_available_repo = MagicMock()
        mock_available_repo.get_enriched_available_fields = AsyncMock(
            return_value=[
                {
                    "field_key": "guest_phone_last4",
                    "display_name": "Guest Phone (Last 4)",
                    "sample_value": None,
                    "source": "builtin",
                }
            ]
        )
        mock_available_repo_cls.return_value = mock_available_repo

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
        assert len(available) >= 1

        # Verify guest_phone_last4 (built-in) is in the response
        field_keys = [f["field_key"] for f in available]
        assert "guest_phone_last4" in field_keys

    @pytest.mark.asyncio
    @patch("src.api.custom_fields.ListingRepository")
    async def test_get_available_custom_fields_listing_not_found(
        self,
        mock_listing_repo_cls: MagicMock,
        mock_db_session: AsyncMock,
    ) -> None:
        """Test endpoint raises 404 when listing not found."""
        # Mock ListingRepository.get_by_id to return None
        mock_listing_repo = MagicMock()
        mock_listing_repo.get_by_id = AsyncMock(return_value=None)
        mock_listing_repo_cls.return_value = mock_listing_repo

        # Call should raise HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_available_custom_fields(
                listing_id=999,
                db=mock_db_session,
            )

        assert exc_info.value.status_code == 404
        assert "not found" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    @patch("src.api.custom_fields.AvailableFieldRepository")
    @patch("src.api.custom_fields.ListingRepository")
    async def test_get_available_custom_fields_returns_proper_format(
        self,
        mock_listing_repo_cls: MagicMock,
        mock_available_repo_cls: MagicMock,
        mock_db_session: AsyncMock,
        mock_listing: Listing,
    ) -> None:
        """Test endpoint returns fields in proper format (list of objects)."""
        # Mock ListingRepository.get_by_id
        mock_listing_repo = MagicMock()
        mock_listing_repo.get_by_id = AsyncMock(return_value=mock_listing)
        mock_listing_repo_cls.return_value = mock_listing_repo

        # Mock AvailableFieldRepository with complete field data
        mock_available_repo = MagicMock()
        mock_available_repo.get_enriched_available_fields = AsyncMock(
            return_value=[
                {
                    "field_key": "guestName",
                    "display_name": "Guest Name",
                    "sample_value": "John Doe",
                    "source": "default",
                },
                {
                    "field_key": "guest_phone_last4",
                    "display_name": "Guest Phone (Last 4)",
                    "sample_value": None,
                    "source": "builtin",
                },
            ]
        )
        mock_available_repo_cls.return_value = mock_available_repo

        # Call the endpoint
        response = await get_available_custom_fields(
            listing_id=1,
            db=mock_db_session,
        )

        available = response["available_fields"]

        # Should include mocked fields
        assert len(available) == 2

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
