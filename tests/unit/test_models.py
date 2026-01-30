# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for database models."""

from datetime import datetime, timedelta

import pytest
from src.models import Booking, CustomField, Listing, OAuthCredential


class TestOAuthCredential:
    """Tests for OAuthCredential model."""

    def test_create_oauth_credential(self, encryption_key):
        """Test creating an OAuth credential."""
        cred = OAuthCredential(
            client_id="test_client",
        )
        cred.client_secret = "test_secret"

        assert cred.client_id == "test_client"
        assert cred.client_secret == "test_secret"

    def test_token_encryption(self, encryption_key):
        """Test that tokens are encrypted."""
        cred = OAuthCredential(client_id="test")
        cred.client_secret = "secret"
        cred.access_token = "access_token_value"
        cred.refresh_token = "refresh_token_value"

        # Encrypted values should be different from plain values
        assert cred._access_token != "access_token_value"
        assert cred._refresh_token != "refresh_token_value"

        # Decrypted values should match originals
        assert cred.access_token == "access_token_value"
        assert cred.refresh_token == "refresh_token_value"

    def test_token_expired_when_no_expiry(self, encryption_key):
        """Test token is considered expired when no expiry set."""
        cred = OAuthCredential(client_id="test")
        cred.client_secret = "secret"
        cred.token_expires_at = None

        assert cred.is_token_expired() is True

    def test_token_expired_when_past_expiry(self, encryption_key):
        """Test token is expired when past expiry time."""
        cred = OAuthCredential(client_id="test")
        cred.client_secret = "secret"
        cred.token_expires_at = datetime.utcnow() - timedelta(hours=1)

        assert cred.is_token_expired() is True

    def test_token_not_expired(self, encryption_key):
        """Test token is not expired when before expiry."""
        cred = OAuthCredential(client_id="test")
        cred.client_secret = "secret"
        cred.token_expires_at = datetime.utcnow() + timedelta(hours=1)

        assert cred.is_token_expired() is False

    def test_repr(self, encryption_key):
        """Test string representation."""
        cred = OAuthCredential(client_id="test_id")
        cred.client_secret = "secret"
        cred.id = 1

        assert "OAuthCredential" in repr(cred)
        assert "test_id" in repr(cred)


class TestListing:
    """Tests for Listing model."""

    def test_create_listing(self):
        """Test creating a listing."""
        listing = Listing(
            cloudbeds_id="property_123",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=False,
            sync_enabled=True,
            timezone="UTC",
        )

        assert listing.cloudbeds_id == "property_123"
        assert listing.name == "Test Property"
        assert listing.enabled is False
        assert listing.timezone == "UTC"
        assert listing.sync_enabled is True

    def test_listing_fields(self):
        """Test listing field values."""
        listing = Listing(
            cloudbeds_id="prop",
            name="Test",
            ical_url_slug="test",
            enabled=False,
            sync_enabled=True,
            timezone="UTC",
        )

        assert listing.enabled is False
        assert listing.sync_enabled is True
        assert listing.timezone == "UTC"
        assert listing.last_sync_at is None
        assert listing.last_sync_error is None

    def test_repr(self):
        """Test string representation."""
        listing = Listing(
            cloudbeds_id="prop",
            name="My Property",
            ical_url_slug="my-prop",
        )
        listing.id = 1

        assert "Listing" in repr(listing)
        assert "My Property" in repr(listing)


class TestCustomField:
    """Tests for CustomField model."""

    @pytest.mark.asyncio
    async def test_create_custom_field(self, async_session):
        """Test creating a custom field requires listing."""
        # First create a listing (required FK)
        listing = Listing(
            cloudbeds_id="cf_listing",
            name="CF Test Listing",
            ical_url_slug="cf-listing",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        field = CustomField(
            listing_id=listing.id,
            field_name="phone_last4",
            display_label="Phone (last 4)",
            enabled=True,
            sort_order=0,
        )

        assert field.field_name == "phone_last4"
        assert field.display_label == "Phone (last 4)"
        assert field.enabled is True
        assert field.sort_order == 0

    @pytest.mark.asyncio
    async def test_repr(self, async_session):
        """Test string representation."""
        listing = Listing(
            cloudbeds_id="repr_listing",
            name="Repr Listing",
            ical_url_slug="repr-listing",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        field = CustomField(
            listing_id=listing.id,
            field_name="guest_name",
            display_label="Guest Name",
            enabled=True,
            sort_order=0,
        )
        async_session.add(field)
        await async_session.flush()

        assert "CustomField" in repr(field)
        assert "guest_name" in repr(field)


class TestBooking:
    """Tests for Booking model."""

    @pytest.mark.asyncio
    async def test_create_booking(self, async_session):
        """Test creating a booking."""
        # Create listing first (required FK)
        listing = Listing(
            cloudbeds_id="booking_test",
            name="Booking Test",
            ical_url_slug="booking-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="booking_123",
            guest_name="John Doe",
            check_in_date=datetime(2026, 2, 1),
            check_out_date=datetime(2026, 2, 5),
            status="confirmed",
        )

        assert booking.cloudbeds_booking_id == "booking_123"
        assert booking.guest_name == "John Doe"
        assert booking.status == "confirmed"

    @pytest.mark.asyncio
    async def test_booking_optional_fields(self, async_session):
        """Test booking optional fields default to None."""
        listing = Listing(
            cloudbeds_id="opt_test",
            name="Optional Test",
            ical_url_slug="opt-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="b1",
            guest_name="Test",
            check_in_date=datetime(2026, 1, 1),
            check_out_date=datetime(2026, 1, 2),
            status="new",
        )

        assert booking.guest_phone_last4 is None
        assert booking.custom_data is None

    @pytest.mark.asyncio
    async def test_event_title_property(self, async_session):
        """Test event_title property returns guest name or booking ID."""
        listing = Listing(
            cloudbeds_id="title_test",
            name="Title Test",
            ical_url_slug="title-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        # With guest name
        booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="BK123",
            guest_name="Jane Doe",
            check_in_date=datetime(2026, 1, 1),
            check_out_date=datetime(2026, 1, 2),
            status="confirmed",
        )
        assert booking.event_title == "Jane Doe"

        # Without guest name
        booking2 = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="BK456",
            guest_name=None,
            check_in_date=datetime(2026, 1, 3),
            check_out_date=datetime(2026, 1, 4),
            status="confirmed",
        )
        assert booking2.event_title == "BK456"

    @pytest.mark.asyncio
    async def test_repr(self, async_session):
        """Test string representation."""
        listing = Listing(
            cloudbeds_id="repr_book",
            name="Repr Book",
            ical_url_slug="repr-book",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="BOOK456",
            guest_name="Jane",
            check_in_date=datetime(2026, 1, 1),
            check_out_date=datetime(2026, 1, 2),
            status="confirmed",
        )
        async_session.add(booking)
        await async_session.flush()

        assert "Booking" in repr(booking)
        assert "BOOK456" in repr(booking)


class TestModelPersistence:
    """Tests for model persistence to database."""

    @pytest.mark.asyncio
    async def test_listing_crud(self, async_session):
        """Test listing CRUD operations."""
        listing = Listing(
            cloudbeds_id="persist_test",
            name="Persistence Test",
            ical_url_slug="persist-test",
        )
        async_session.add(listing)
        await async_session.flush()

        assert listing.id is not None
        assert listing.created_at is not None

    @pytest.mark.asyncio
    async def test_booking_with_listing_fk(self, async_session):
        """Test booking with listing foreign key."""
        listing = Listing(
            cloudbeds_id="fk_test",
            name="FK Test",
            ical_url_slug="fk-test",
        )
        async_session.add(listing)
        await async_session.flush()

        booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="fk_booking",
            guest_name="FK Guest",
            check_in_date=datetime(2026, 3, 1),
            check_out_date=datetime(2026, 3, 5),
            status="confirmed",
        )
        async_session.add(booking)
        await async_session.flush()

        assert booking.id is not None
        assert booking.listing_id == listing.id

    @pytest.mark.asyncio
    async def test_custom_field_with_listing(self, async_session):
        """Test custom field with listing relationship."""
        listing = Listing(
            cloudbeds_id="cf_test",
            name="Custom Field Test",
            ical_url_slug="cf-test",
            enabled=True,
            sync_enabled=True,
            timezone="UTC",
        )
        async_session.add(listing)
        await async_session.flush()

        field = CustomField(
            listing_id=listing.id,
            field_name="phone_last4",
            display_label="Phone (last 4)",
            enabled=True,
            sort_order=1,
        )
        async_session.add(field)
        await async_session.flush()

        assert field.id is not None
        assert field.listing_id == listing.id
