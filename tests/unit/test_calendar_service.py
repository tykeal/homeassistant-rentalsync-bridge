# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for calendar service."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from src.models.booking import Booking
from src.models.custom_field import CustomField
from src.models.listing import Listing
from src.services.calendar_service import (
    CalendarCache,
    CalendarService,
)


class TestCalendarCache:
    """Tests for CalendarCache."""

    def test_set_and_get(self):
        """Test basic set and get."""
        cache = CalendarCache(ttl_seconds=60)
        cache.set("key1", "value1")

        assert cache.get("key1") == "value1"

    def test_get_missing_key(self):
        """Test get returns None for missing key."""
        cache = CalendarCache()

        assert cache.get("nonexistent") is None

    def test_cache_expiry(self):
        """Test cache entries expire after TTL."""
        cache = CalendarCache(ttl_seconds=0)  # Immediate expiry
        cache.set("key", "value")

        # Should be expired immediately
        assert cache.get("key") is None

    def test_invalidate(self):
        """Test invalidating a cache entry."""
        cache = CalendarCache()
        cache.set("key", "value")

        cache.invalidate("key")

        assert cache.get("key") is None

    def test_invalidate_prefix(self):
        """Test invalidating cache entries by prefix."""
        cache = CalendarCache()
        cache.set("listing-1/room-a", "value1")
        cache.set("listing-1/room-b", "value2")
        cache.set("listing-2/room-a", "value3")

        cache.invalidate_prefix("listing-1")

        # listing-1 entries should be gone
        assert cache.get("listing-1/room-a") is None
        assert cache.get("listing-1/room-b") is None
        # listing-2 entries should remain
        assert cache.get("listing-2/room-a") == "value3"

    def test_invalidate_prefix_does_not_match_similar_slugs(self):
        """Test prefix invalidation doesn't affect similar but distinct slugs."""
        cache = CalendarCache()
        # Similar slug prefixes that should NOT be invalidated together
        cache.set("beach-house/room-1", "value1")
        cache.set("beach-house-deluxe/room-1", "value2")
        cache.set("beach-house-premium/room-1", "value3")

        cache.invalidate_prefix("beach-house")

        # Only beach-house entries should be gone
        assert cache.get("beach-house/room-1") is None
        # Similar slugs should remain untouched
        assert cache.get("beach-house-deluxe/room-1") == "value2"
        assert cache.get("beach-house-premium/room-1") == "value3"

    def test_clear(self):
        """Test clearing all cache entries."""
        cache = CalendarCache()
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        cache.clear()

        assert cache.get("key1") is None
        assert cache.get("key2") is None


class TestCalendarService:
    """Tests for CalendarService."""

    @pytest.fixture
    def cache(self):
        """Create a fresh cache for each test."""
        return CalendarCache()

    @pytest.fixture
    def service(self, cache):
        """Create calendar service with test cache."""
        return CalendarService(cache=cache)

    @pytest.fixture
    def listing(self):
        """Create test listing."""
        listing = Listing(
            cloudbeds_id="test_prop",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
            timezone="America/New_York",
        )
        listing.id = 1
        return listing

    @pytest.fixture
    def booking(self, listing):
        """Create test booking."""
        booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="BK12345",
            guest_name="John Smith",
            guest_phone_last4="1234",
            check_in_date=datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 5, 11, 0, tzinfo=UTC),
            status="confirmed",
            custom_data={"booking_notes": "VIP guest"},
        )
        booking.id = 1
        return booking

    def test_generate_ical_basic(self, service, listing, booking):
        """Test basic iCal generation."""
        ical = service.generate_ical(listing, [booking])

        assert "BEGIN:VCALENDAR" in ical
        assert "END:VCALENDAR" in ical
        assert "BEGIN:VEVENT" in ical
        assert "END:VEVENT" in ical

    def test_generate_ical_includes_guest_name(self, service, listing, booking):
        """Test iCal includes guest name as summary."""
        ical = service.generate_ical(listing, [booking])

        assert "SUMMARY:John Smith" in ical

    def test_generate_ical_includes_phone_last4(self, service, listing, booking):
        """Test iCal includes phone last 4 in description."""
        ical = service.generate_ical(listing, [booking])

        assert "1234" in ical

    def test_generate_ical_includes_booking_id(self, service, listing, booking):
        """Test iCal includes booking ID in description."""
        ical = service.generate_ical(listing, [booking])

        assert "BK12345" in ical

    def test_generate_ical_guest_name_fallback(self, service, listing):
        """Test guest name fallback to booking ID."""
        booking = Booking(
            listing_id=listing.id,
            cloudbeds_booking_id="FALLBACK123",
            guest_name=None,  # No guest name
            check_in_date=datetime(2026, 3, 1, tzinfo=UTC),
            check_out_date=datetime(2026, 3, 5, tzinfo=UTC),
            status="confirmed",
        )
        booking.id = 2

        ical = service.generate_ical(listing, [booking])

        assert "SUMMARY:FALLBACK123" in ical

    def test_generate_ical_caching(self, service, listing, booking, cache):
        """Test iCal is cached after generation."""
        # First call - should generate and cache
        ical1 = service.generate_ical(listing, [booking])

        # Verify cached
        cached = cache.get(listing.ical_url_slug)
        assert cached == ical1

        # Second call - should return cached
        ical2 = service.generate_ical(listing, [booking])
        assert ical2 == ical1

    def test_invalidate_cache(self, service, listing, booking, cache):
        """Test cache invalidation."""
        service.generate_ical(listing, [booking])

        service.invalidate_cache(listing.ical_url_slug)

        assert cache.get(listing.ical_url_slug) is None

    def test_generate_ical_with_custom_fields(self, service, listing, booking):
        """Test iCal with custom fields."""
        custom_fields = [
            CustomField(
                listing_id=listing.id,
                field_name="booking_notes",
                display_label="Notes",
                enabled=True,
                sort_order=0,
            )
        ]
        custom_fields[0].id = 1

        ical = service.generate_ical(listing, [booking], custom_fields)

        assert "VIP guest" in ical

    def test_generate_ical_calendar_metadata(self, service, listing, booking):
        """Test calendar metadata is set correctly."""
        ical = service.generate_ical(listing, [booking])

        assert "PRODID:-//RentalSync Bridge" in ical
        assert "VERSION:2.0" in ical
        assert "X-WR-CALNAME:Test Property" in ical

    def test_timezone_handling_valid(self, service):
        """Test valid timezone is used."""
        tz = service._get_timezone("America/New_York")

        assert tz == ZoneInfo("America/New_York")

    def test_timezone_handling_invalid_fallback(self, service):
        """Test invalid timezone falls back to UTC."""
        tz = service._get_timezone("Invalid/Timezone")

        assert tz == ZoneInfo("UTC")

    def test_truncate_summary_short(self, service):
        """Test short summary is not truncated."""
        result = CalendarService._truncate_summary("Short name")

        assert result == "Short name"

    def test_truncate_summary_long(self, service):
        """Test long summary is truncated."""
        long_name = "A" * 300
        result = CalendarService._truncate_summary(long_name)

        assert len(result) == 255
        assert result.endswith("...")


class TestExtractPhoneLast4:
    """Tests for phone number extraction."""

    def test_extract_from_full_number(self):
        """Test extracting from full phone number."""
        result = CalendarService.extract_phone_last4("+1 (555) 123-4567")

        assert result == "4567"

    def test_extract_from_digits_only(self):
        """Test extracting from digits-only number."""
        result = CalendarService.extract_phone_last4("5551234567")

        assert result == "4567"

    def test_extract_short_number(self):
        """Test extraction from too-short number."""
        result = CalendarService.extract_phone_last4("123")

        assert result is None

    def test_extract_none(self):
        """Test extraction from None."""
        result = CalendarService.extract_phone_last4(None)

        assert result is None

    def test_extract_empty_string(self):
        """Test extraction from empty string."""
        result = CalendarService.extract_phone_last4("")

        assert result is None

    def test_extract_exactly_four_digits(self):
        """Test extraction from exactly 4 digits."""
        result = CalendarService.extract_phone_last4("1234")

        assert result == "1234"


class TestListingSpecificCustomFields:
    """Tests for listing-specific custom field configurations."""

    def test_different_listings_use_different_custom_fields(self):
        """Test that each listing uses its own custom field configuration."""
        service = CalendarService(cache=CalendarCache())

        # Create two listings
        listing1 = Listing(
            id=1,
            cloudbeds_id="PROP1",
            name="Beach House",
            enabled=True,
            sync_enabled=True,
            ical_url_slug="beach-house",
            timezone="America/Los_Angeles",
        )
        listing2 = Listing(
            id=2,
            cloudbeds_id="PROP2",
            name="Mountain Cabin",
            enabled=True,
            sync_enabled=True,
            ical_url_slug="mountain-cabin",
            timezone="America/Denver",
        )

        # Create bookings for each listing
        booking1 = Booking(
            id=1,
            listing_id=1,
            cloudbeds_booking_id="CB001",
            guest_name="Guest One",
            check_in_date=datetime(2024, 7, 1, tzinfo=UTC),
            check_out_date=datetime(2024, 7, 5, tzinfo=UTC),
            status="confirmed",
            custom_data={"booking_notes": "Beach lover"},
        )
        booking2 = Booking(
            id=2,
            listing_id=2,
            cloudbeds_booking_id="CB002",
            guest_name="Guest Two",
            check_in_date=datetime(2024, 8, 1, tzinfo=UTC),
            check_out_date=datetime(2024, 8, 5, tzinfo=UTC),
            status="confirmed",
            custom_data={"special_requests": "Mountain view room"},
        )

        # Create different custom fields for each listing
        fields1 = [
            CustomField(
                id=1,
                listing_id=1,
                field_name="booking_notes",
                display_label="Notes",
                enabled=True,
                sort_order=0,
            )
        ]
        fields2 = [
            CustomField(
                id=2,
                listing_id=2,
                field_name="special_requests",
                display_label="Special Requests",
                enabled=True,
                sort_order=0,
            )
        ]

        # Generate iCal for each
        ical1 = service.generate_ical(listing1, [booking1], fields1)
        service.invalidate_cache(listing1.ical_url_slug)

        ical2 = service.generate_ical(listing2, [booking2], fields2)

        # Verify listing 1 has its custom field
        assert "Beach lover" in ical1
        assert "Mountain view room" not in ical1

        # Verify listing 2 has its custom field
        assert "Mountain view room" in ical2
        assert "Beach lover" not in ical2
