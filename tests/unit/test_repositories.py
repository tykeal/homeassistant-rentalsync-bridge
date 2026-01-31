# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for repository classes."""

from datetime import UTC, datetime, timedelta

import pytest
from src.models import Booking, CustomField, Listing
from src.repositories.booking_repository import BookingRepository
from src.repositories.custom_field_repository import (
    AVAILABLE_FIELDS,
    CustomFieldRepository,
)
from src.repositories.listing_repository import ListingRepository


class TestListingRepository:
    """Tests for ListingRepository."""

    @pytest.mark.asyncio
    async def test_create_listing(self, async_session):
        """Test creating a listing."""
        repo = ListingRepository(async_session)
        listing = Listing(
            cloudbeds_id="test_prop_1",
            name="Test Property",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )

        created = await repo.create(listing)

        assert created.id is not None
        assert created.ical_url_slug == "test-property"

    @pytest.mark.asyncio
    async def test_get_by_slug(self, async_session):
        """Test getting listing by slug."""
        repo = ListingRepository(async_session)
        listing = Listing(
            cloudbeds_id="slug_test",
            name="Slug Test",
            ical_url_slug="my-custom-slug",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        await repo.create(listing)

        found = await repo.get_by_slug("my-custom-slug")

        assert found is not None
        assert found.name == "Slug Test"

    @pytest.mark.asyncio
    async def test_get_by_cloudbeds_id(self, async_session):
        """Test getting listing by Cloudbeds ID."""
        repo = ListingRepository(async_session)
        listing = Listing(
            cloudbeds_id="cb_12345",
            name="CB Test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        await repo.create(listing)

        found = await repo.get_by_cloudbeds_id("cb_12345")

        assert found is not None
        assert found.name == "CB Test"

    @pytest.mark.asyncio
    async def test_get_enabled(self, async_session):
        """Test getting only enabled listings."""
        repo = ListingRepository(async_session)
        await repo.create(
            Listing(
                cloudbeds_id="enabled_1",
                name="Enabled 1",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )
        await repo.create(
            Listing(
                cloudbeds_id="disabled_1",
                name="Disabled 1",
                enabled=False,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        enabled = await repo.get_enabled()

        assert len(enabled) == 1
        assert enabled[0].name == "Enabled 1"

    @pytest.mark.asyncio
    async def test_slugify(self, async_session):
        """Test slug generation from name."""
        repo = ListingRepository(async_session)

        assert repo._slugify("Test Property") == "test-property"
        assert repo._slugify("  Spaces  ") == "spaces"
        assert repo._slugify("Special!@#Chars") == "specialchars"
        assert repo._slugify("Multiple---Hyphens") == "multiple-hyphens"

    @pytest.mark.asyncio
    async def test_unique_slug_generation(self, async_session):
        """Test unique slug generation on collision."""
        repo = ListingRepository(async_session)

        listing1 = Listing(
            cloudbeds_id="prop_1",
            name="Beach House",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        await repo.create(listing1)

        listing2 = Listing(
            cloudbeds_id="prop_2",
            name="Beach House",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        await repo.create(listing2)

        assert listing1.ical_url_slug == "beach-house"
        assert listing2.ical_url_slug != "beach-house"
        assert listing2.ical_url_slug.startswith("beach-house-")


class TestBookingRepository:
    """Tests for BookingRepository."""

    @pytest.mark.asyncio
    async def test_create_booking(self, async_session):
        """Test creating a booking."""
        # Create listing first
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="booking_test",
                name="Booking Test",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = BookingRepository(async_session)
        booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="BK001",
            guest_name="John Doe",
            check_in_date=datetime(2026, 3, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 5, tzinfo=UTC),
            status="confirmed",
        )

        created = await repo.create(booking)

        assert created.id is not None
        assert created.guest_name == "John Doe"

    @pytest.mark.asyncio
    async def test_get_confirmed_for_listing(self, async_session):
        """Test getting active bookings (confirmed, checked_in, checked_out)."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="confirmed_test",
                name="Confirmed Test",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = BookingRepository(async_session)
        # Create confirmed booking
        await repo.create(
            Booking(
                listing_id=listing.id,
                cloudbeds_booking_id="BK_CONF",
                guest_name="Confirmed Guest",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=5),
                status="confirmed",
            )
        )
        # Create checked_in booking
        await repo.create(
            Booking(
                listing_id=listing.id,
                cloudbeds_booking_id="BK_CHECKIN",
                guest_name="Checked In Guest",
                check_in_date=datetime.now(UTC) - timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=3),
                status="checked_in",
            )
        )
        # Create checked_out booking (recent)
        await repo.create(
            Booking(
                listing_id=listing.id,
                cloudbeds_booking_id="BK_CHECKOUT",
                guest_name="Checked Out Guest",
                check_in_date=datetime.now(UTC) - timedelta(days=5),
                check_out_date=datetime.now(UTC) - timedelta(days=1),
                status="checked_out",
            )
        )
        # Create cancelled booking (should be excluded)
        await repo.create(
            Booking(
                listing_id=listing.id,
                cloudbeds_booking_id="BK_CANCEL",
                guest_name="Cancelled Guest",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=5),
                status="cancelled",
            )
        )

        confirmed = await repo.get_confirmed_for_listing(listing.id)

        assert len(confirmed) == 3
        guest_names = {b.guest_name for b in confirmed}
        assert "Confirmed Guest" in guest_names
        assert "Checked In Guest" in guest_names
        assert "Checked Out Guest" in guest_names
        assert "Cancelled Guest" not in guest_names

    @pytest.mark.asyncio
    async def test_upsert_insert(self, async_session):
        """Test upsert creates new booking."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="upsert_test",
                name="Upsert Test",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = BookingRepository(async_session)
        booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="BK_NEW",
            guest_name="New Guest",
            check_in_date=datetime.now(UTC) + timedelta(days=1),
            check_out_date=datetime.now(UTC) + timedelta(days=5),
            status="confirmed",
        )

        result, was_created = await repo.upsert(booking)

        assert was_created is True
        assert result.id is not None

    @pytest.mark.asyncio
    async def test_upsert_update(self, async_session):
        """Test upsert updates existing booking."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="upsert_update",
                name="Upsert Update",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = BookingRepository(async_session)
        original = await repo.create(
            Booking(
                listing_id=listing.id,
                cloudbeds_booking_id="BK_UPDATE",
                guest_name="Original Name",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=5),
                status="confirmed",
            )
        )

        updated_booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="BK_UPDATE",
            guest_name="Updated Name",
            check_in_date=datetime.now(UTC) + timedelta(days=1),
            check_out_date=datetime.now(UTC) + timedelta(days=5),
            status="confirmed",
        )

        result, was_created = await repo.upsert(updated_booking)

        assert was_created is False
        assert result.id == original.id
        assert result.guest_name == "Updated Name"

    @pytest.mark.asyncio
    async def test_mark_cancelled(self, async_session):
        """Test marking booking as cancelled."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="cancel_test",
                name="Cancel Test",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = BookingRepository(async_session)
        booking = await repo.create(
            Booking(
                listing_id=listing.id,
                cloudbeds_booking_id="BK_TO_CANCEL",
                guest_name="To Cancel",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=5),
                status="confirmed",
            )
        )

        cancelled = await repo.mark_cancelled(booking)

        assert cancelled.status == "cancelled"


class TestCustomFieldRepository:
    """Tests for CustomFieldRepository."""

    @pytest.mark.asyncio
    async def test_create_custom_field(self, async_session):
        """Test creating a custom field."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="cf_test",
                name="CF Test",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = CustomFieldRepository(async_session)
        field = CustomField(
            listing_id=listing.id,
            field_name="booking_notes",
            display_label="Notes",
            enabled=True,
            sort_order=0,
        )

        created = await repo.create(field)

        assert created.id is not None
        assert created.field_name == "booking_notes"

    @pytest.mark.asyncio
    async def test_create_invalid_field_name(self, async_session):
        """Test creating field with invalid name raises error."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="invalid_cf",
                name="Invalid CF",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = CustomFieldRepository(async_session)
        field = CustomField(
            listing_id=listing.id,
            field_name="invalid_field_name",
            display_label="Invalid",
            enabled=True,
            sort_order=0,
        )

        with pytest.raises(ValueError, match="Invalid field_name"):
            await repo.create(field)

    @pytest.mark.asyncio
    async def test_get_enabled_for_listing(self, async_session):
        """Test getting only enabled custom fields."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="enabled_cf",
                name="Enabled CF",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = CustomFieldRepository(async_session)
        await repo.create(
            CustomField(
                listing_id=listing.id,
                field_name="booking_notes",
                display_label="Notes",
                enabled=True,
                sort_order=0,
            )
        )
        await repo.create(
            CustomField(
                listing_id=listing.id,
                field_name="arrival_time",
                display_label="Arrival",
                enabled=False,
                sort_order=1,
            )
        )

        enabled = await repo.get_enabled_for_listing(listing.id)

        assert len(enabled) == 1
        assert enabled[0].field_name == "booking_notes"

    @pytest.mark.asyncio
    async def test_create_defaults_for_listing(self, async_session):
        """Test creating default custom fields."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="defaults_cf",
                name="Defaults CF",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = CustomFieldRepository(async_session)
        created = await repo.create_defaults_for_listing(listing.id)

        assert len(created) >= 1
        field_names = [f.field_name for f in created]
        assert "booking_notes" in field_names

    def test_available_fields(self):
        """Test available fields dictionary."""
        fields = CustomFieldRepository.get_available_fields()

        assert "booking_notes" in fields
        assert "arrival_time" in fields
        assert len(fields) == len(AVAILABLE_FIELDS)
