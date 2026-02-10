# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for repository classes."""

from datetime import UTC, datetime, timedelta

import pytest
from src.models import Booking, CustomField, Listing
from src.repositories.available_field_repository import (
    AvailableFieldRepository,
    should_exclude_field,
)
from src.repositories.booking_repository import BookingRepository
from src.repositories.custom_field_repository import (
    BUILTIN_FIELDS,
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
    async def test_create_custom_field_builtin(self, async_session):
        """Test creating a built-in custom field."""
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
            field_name="guest_phone_last4",
            display_label="Phone Last 4",
            enabled=True,
            sort_order=0,
        )

        created = await repo.create(field)

        assert created.id is not None
        assert created.field_name == "guest_phone_last4"

    @pytest.mark.asyncio
    async def test_create_custom_field_discovered(self, async_session):
        """Test creating a custom field from discovered fields.

        Uses a field key not in DEFAULT_CLOUDBEDS_FIELDS to verify
        that discovery is actually required for the field to be valid.
        """
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="cf_discovered_test",
                name="CF Discovered Test",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = CustomFieldRepository(async_session)

        # First, verify field is rejected before discovery
        field_before = CustomField(
            listing_id=listing.id,
            field_name="myCustomApiField",
            display_label="My Custom Field",
            enabled=True,
            sort_order=0,
        )
        with pytest.raises(ValueError, match="Invalid field_name"):
            await repo.create(field_before)

        # Now discover the field
        avail_repo = AvailableFieldRepository(async_session)
        await avail_repo.upsert_field(listing.id, "myCustomApiField", "Sample value")

        # Now creating should succeed
        field_after = CustomField(
            listing_id=listing.id,
            field_name="myCustomApiField",
            display_label="My Custom Field",
            enabled=True,
            sort_order=0,
        )
        created = await repo.create(field_after)

        assert created.id is not None
        assert created.field_name == "myCustomApiField"

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
        # Use built-in field (always available)
        await repo.create(
            CustomField(
                listing_id=listing.id,
                field_name="guest_phone_last4",
                display_label="Phone",
                enabled=True,
                sort_order=0,
            )
        )

        enabled = await repo.get_enabled_for_listing(listing.id)

        assert len(enabled) == 1
        assert enabled[0].field_name == "guest_phone_last4"

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

        # Only built-in fields are created as defaults now
        assert len(created) >= 1
        field_names = [f.field_name for f in created]
        assert "guest_phone_last4" in field_names

    def test_builtin_fields(self):
        """Test built-in fields dictionary."""
        fields = CustomFieldRepository.get_builtin_fields()

        assert "guest_phone_last4" in fields
        assert len(fields) == len(BUILTIN_FIELDS)

    def test_guest_phone_last4_in_builtin_fields(self):
        """Test guest_phone_last4 is in BUILTIN_FIELDS dictionary."""
        fields = CustomFieldRepository.get_builtin_fields()

        assert "guest_phone_last4" in fields
        assert fields["guest_phone_last4"] == "Guest Phone (Last 4 Digits)"

    @pytest.mark.asyncio
    async def test_create_guest_phone_last4_field(self, async_session):
        """Test creating guest_phone_last4 custom field (built-in field)."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="phone_test",
                name="Phone Test",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = CustomFieldRepository(async_session)
        field = CustomField(
            listing_id=listing.id,
            field_name="guest_phone_last4",
            display_label="Guest Phone (Last 4 Digits)",
            enabled=True,
            sort_order=0,
        )

        created = await repo.create(field)

        assert created.id is not None
        assert created.field_name == "guest_phone_last4"
        assert created.display_label == "Guest Phone (Last 4 Digits)"

    def test_default_cloudbeds_fields(self):
        """Test default Cloudbeds fields are available."""
        fields = CustomFieldRepository.get_default_cloudbeds_fields()

        # Should have common Cloudbeds fields
        assert "guestName" in fields
        assert "notes" in fields
        assert "sourceName" in fields
        assert "status" in fields
        # Should have many fields available
        assert len(fields) >= 10

    @pytest.mark.asyncio
    async def test_create_default_cloudbeds_field(self, async_session):
        """Test creating a field from default Cloudbeds fields."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="default_field_test",
                name="Default Field Test",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = CustomFieldRepository(async_session)
        # Use a default Cloudbeds field (no need to discover first)
        field = CustomField(
            listing_id=listing.id,
            field_name="notes",
            display_label="Booking Notes",
            enabled=True,
            sort_order=0,
        )

        created = await repo.create(field)

        assert created.id is not None
        assert created.field_name == "notes"


class TestAvailableFieldRepository:
    """Tests for AvailableFieldRepository."""

    @pytest.mark.asyncio
    async def test_upsert_preserves_falsy_sample_zero(self, async_session):
        """Test that sample_value '0' is preserved (not treated as empty)."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="falsy_test",
                name="Falsy Test",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = AvailableFieldRepository(async_session)
        field = await repo.upsert_field(listing.id, "testAmount", "0")

        assert field is not None
        assert field.sample_value == "0"

    @pytest.mark.asyncio
    async def test_upsert_preserves_falsy_sample_false(self, async_session):
        """Test that sample_value 'false' is preserved."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="falsy_false",
                name="Falsy False Test",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = AvailableFieldRepository(async_session)
        field = await repo.upsert_field(listing.id, "isActive", "false")

        assert field is not None
        assert field.sample_value == "false"

    @pytest.mark.asyncio
    async def test_upsert_empty_string_becomes_none(self, async_session):
        """Test that empty string sample_value becomes None."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="empty_sample",
                name="Empty Sample",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = AvailableFieldRepository(async_session)
        field = await repo.upsert_field(listing.id, "testField", "")

        assert field is not None
        assert field.sample_value is None


class TestShouldExcludeField:
    """Tests for should_exclude_field function."""

    def test_excludes_camelcase_id_suffix(self):
        """Test that camelCase ID suffixes are excluded."""
        assert should_exclude_field("reservationId") is True
        assert should_exclude_field("roomId") is True
        assert should_exclude_field("customerId") is True

    def test_excludes_allcaps_id_suffix(self):
        """Test that ALLCAPS ID suffixes are excluded."""
        assert should_exclude_field("propertyID") is True
        assert should_exclude_field("roomID") is True

    def test_excludes_exact_id(self):
        """Test that exact 'id' is excluded."""
        assert should_exclude_field("id") is True

    def test_does_not_exclude_paid(self):
        """Test that 'paid' is NOT excluded (legitimate field)."""
        assert should_exclude_field("paid") is False

    def test_does_not_exclude_valid(self):
        """Test that 'valid' is NOT excluded."""
        assert should_exclude_field("valid") is False

    def test_does_not_exclude_regular_fields(self):
        """Test that regular fields are not excluded."""
        assert should_exclude_field("guestName") is False
        assert should_exclude_field("balance") is False
        assert should_exclude_field("notes") is False
        assert should_exclude_field("status") is False


class TestDiscoverFieldsFromReservation:
    """Tests for discover_fields_from_reservation method."""

    @pytest.mark.asyncio
    async def test_discovers_top_level_fields(self, async_session):
        """Test that top-level reservation fields are discovered."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="discover_top",
                name="Discover Top Level",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = AvailableFieldRepository(async_session)
        reservation = {
            "guestName": "John Doe",
            "status": "confirmed",
            "balance": "100.00",
        }

        discovered = await repo.discover_fields_from_reservation(
            listing.id, reservation
        )

        field_keys = {f.field_key for f in discovered}
        assert "guestName" in field_keys
        assert "status" in field_keys
        assert "balance" in field_keys

    @pytest.mark.asyncio
    async def test_discovers_room_fields(self, async_session):
        """Test that fields from first room in rooms array are discovered."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="discover_room",
                name="Discover Room Fields",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = AvailableFieldRepository(async_session)
        reservation = {
            "guestName": "Jane Doe",
            "rooms": [
                {"roomTypeName": "Deluxe", "roomName": "Room 101"},
                {"roomTypeName": "Standard", "roomName": "Room 102"},
            ],
        }

        discovered = await repo.discover_fields_from_reservation(
            listing.id, reservation
        )

        field_keys = {f.field_key for f in discovered}
        assert "guestName" in field_keys
        assert "roomTypeName" in field_keys
        assert "roomName" in field_keys

    @pytest.mark.asyncio
    async def test_dedupes_with_already_discovered(self, async_session):
        """Test that already_discovered set prevents duplicate processing."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="discover_dedupe",
                name="Discover Dedupe",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = AvailableFieldRepository(async_session)
        already_discovered: set[str] = {"guestName", "status"}
        reservation = {
            "guestName": "John Doe",
            "status": "confirmed",
            "balance": "100.00",
        }

        discovered = await repo.discover_fields_from_reservation(
            listing.id, reservation, already_discovered
        )

        # Only balance should be discovered (guestName and status were in set)
        field_keys = {f.field_key for f in discovered}
        assert "balance" in field_keys
        assert "guestName" not in field_keys
        assert "status" not in field_keys
        # already_discovered should be updated
        assert "balance" in already_discovered

    @pytest.mark.asyncio
    async def test_excludes_id_fields(self, async_session):
        """Test that ID fields are excluded from discovery."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="discover_exclude_id",
                name="Discover Exclude ID",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = AvailableFieldRepository(async_session)
        reservation = {
            "reservationId": "12345",
            "propertyID": "67890",
            "id": "99999",
            "guestName": "John Doe",
            "paid": "50.00",  # Should NOT be excluded
        }

        discovered = await repo.discover_fields_from_reservation(
            listing.id, reservation
        )

        field_keys = {f.field_key for f in discovered}
        assert "reservationId" not in field_keys
        assert "propertyID" not in field_keys
        assert "id" not in field_keys
        assert "guestName" in field_keys
        assert "paid" in field_keys

    @pytest.mark.asyncio
    async def test_skips_complex_values(self, async_session):
        """Test that dict and list values are skipped."""
        listing_repo = ListingRepository(async_session)
        listing = await listing_repo.create(
            Listing(
                cloudbeds_id="discover_skip_complex",
                name="Discover Skip Complex",
                enabled=True,
                sync_enabled=True,
                timezone="UTC",
            )
        )

        repo = AvailableFieldRepository(async_session)
        reservation = {
            "guestName": "John Doe",
            "rooms": [{"roomName": "101"}],  # List - skipped at top level
            "customData": {"key": "value"},  # Dict - skipped
            "status": "confirmed",
        }

        discovered = await repo.discover_fields_from_reservation(
            listing.id, reservation
        )

        field_keys = {f.field_key for f in discovered}
        assert "guestName" in field_keys
        assert "status" in field_keys
        assert "rooms" not in field_keys
        assert "customData" not in field_keys
        # Room fields should still be discovered from rooms array
        assert "roomName" in field_keys
