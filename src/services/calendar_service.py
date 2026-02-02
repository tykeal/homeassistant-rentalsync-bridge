# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Calendar service for iCal feed generation."""

import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import Calendar, Event

from src.models.booking import Booking
from src.models.custom_field import CustomField
from src.models.listing import Listing

logger = logging.getLogger(__name__)

# Cache TTL in seconds (5 minutes)
CACHE_TTL_SECONDS = 300

# Minimum phone digits required
MIN_PHONE_DIGITS = 4


class CalendarCache:
    """Simple in-memory cache for generated iCal strings."""

    def __init__(self, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        """Initialize cache with TTL.

        Args:
            ttl_seconds: Time-to-live for cache entries.
        """
        self._cache: dict[str, tuple[str, datetime]] = {}
        self._ttl = timedelta(seconds=ttl_seconds)

    def get(self, key: str) -> str | None:
        """Get cached value if not expired.

        Args:
            key: Cache key (typically listing slug).

        Returns:
            Cached iCal string or None if expired/missing.
        """
        if key not in self._cache:
            return None

        value, timestamp = self._cache[key]
        if datetime.now(UTC) - timestamp > self._ttl:
            del self._cache[key]
            return None

        return value

    def set(self, key: str, value: str) -> None:
        """Store value in cache.

        Args:
            key: Cache key.
            value: iCal string to cache.
        """
        self._cache[key] = (value, datetime.now(UTC))

    def invalidate(self, key: str) -> None:
        """Remove entry from cache.

        Args:
            key: Cache key to invalidate.
        """
        self._cache.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        """Remove all cache entries with keys starting with prefix.

        Used to invalidate all room-level caches for a listing when bookings change.

        Args:
            prefix: Key prefix to match (e.g., listing slug).
        """
        keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
        for key in keys_to_remove:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()


# Global cache instance
_calendar_cache = CalendarCache()


def get_calendar_cache() -> CalendarCache:
    """Get the global calendar cache instance.

    Returns:
        CalendarCache singleton.
    """
    return _calendar_cache


class CalendarService:
    """Service for generating iCal feeds from booking data.

    Generates RFC 5545 compliant iCal calendars with proper timezone
    handling and customizable event descriptions.
    """

    def __init__(self, cache: CalendarCache | None = None) -> None:
        """Initialize calendar service.

        Args:
            cache: Optional cache instance. Uses global cache if not provided.
        """
        self._cache = cache or get_calendar_cache()

    def generate_ical(
        self,
        listing: Listing,
        bookings: Sequence[Booking],
        custom_fields: Sequence[CustomField] | None = None,
        room_slug: str | None = None,
    ) -> str:
        """Generate iCal feed for a listing or room.

        Args:
            listing: Listing to generate calendar for.
            bookings: Confirmed bookings (already filtered by room if needed).
            custom_fields: Enabled custom fields for description.
            room_slug: Optional room slug for cache key generation.

        Returns:
            iCal string (text/calendar format).
        """
        # Generate cache key based on listing and optionally room
        if room_slug:
            cache_key = f"{listing.ical_url_slug}/{room_slug}"
        else:
            cache_key = listing.ical_url_slug

        # Check cache first
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug("Cache hit for %s", cache_key)
            return cached

        # Generate calendar
        cal = self._create_calendar(listing)

        # Get timezone for event dates
        tz = self._get_timezone(listing.timezone)

        # Add events for each booking
        for booking in bookings:
            event = self._create_event(booking, tz, custom_fields)
            cal.add_component(event)

        # Convert to string
        ical_bytes = cal.to_ical()
        ical_string = cast("bytes", ical_bytes).decode("utf-8")

        # Cache result
        self._cache.set(cache_key, ical_string)
        logger.debug("Generated and cached iCal for %s", cache_key)

        return ical_string

    def invalidate_cache(self, listing_slug: str, room_slug: str | None = None) -> None:
        """Invalidate cached iCal for a listing or room.

        Args:
            listing_slug: Listing URL slug to invalidate.
            room_slug: Optional room URL slug for room-level invalidation.
        """
        cache_key = f"{listing_slug}/{room_slug}" if room_slug else listing_slug
        self._cache.invalidate(cache_key)

    def _create_calendar(self, listing: Listing) -> Calendar:
        """Create iCal calendar object with metadata.

        Args:
            listing: Listing for calendar metadata.

        Returns:
            Configured Calendar object.
        """
        cal = Calendar()
        cal.add("prodid", "-//RentalSync Bridge//rentalsync-bridge//EN")
        cal.add("version", "2.0")
        cal.add("calscale", "GREGORIAN")
        cal.add("method", "PUBLISH")
        cal.add("x-wr-calname", listing.name)
        cal.add("x-wr-timezone", listing.timezone)
        return cal

    def _create_event(
        self,
        booking: Booking,
        tz: ZoneInfo,
        custom_fields: Sequence[CustomField] | None = None,
    ) -> Event:
        """Create iCal event for a booking.

        Args:
            booking: Booking data for the event.
            tz: Timezone for date handling.
            custom_fields: Custom fields for description.

        Returns:
            Configured Event object.
        """
        event = Event()

        # Generate unique ID based on booking
        uid = self._generate_uid(booking)
        event.add("uid", uid)

        # Summary: guest name or booking ID fallback
        summary = booking.event_title
        event.add("summary", self._truncate_summary(summary))

        # All-day event dates with timezone
        dtstart = self._to_date_with_tz(booking.check_in_date, tz)
        dtend = self._to_date_with_tz(booking.check_out_date, tz)

        event.add("dtstart", dtstart)
        event.add("dtend", dtend)

        # Description with phone last 4 and custom fields
        description = self._build_description(booking, custom_fields)
        if description:
            event.add("description", description)

        # Timestamps
        event.add("dtstamp", datetime.now(UTC))
        event.add("created", datetime.now(UTC))

        # Status
        event.add("status", "CONFIRMED")
        event.add("transp", "OPAQUE")

        return event

    def _build_description(
        self,
        booking: Booking,
        custom_fields: Sequence[CustomField] | None = None,
    ) -> str:
        """Build event description from booking data.

        Args:
            booking: Booking with guest data.
            custom_fields: Enabled custom fields to include.

        Returns:
            Formatted description string.
        """
        lines: list[str] = []

        # Always include phone last 4 if available
        if booking.guest_phone_last4:
            lines.append(f"Phone (last 4): {booking.guest_phone_last4}")

        # Add custom fields from booking's custom_data
        if custom_fields and booking.custom_data:
            for field in custom_fields:
                if not field.enabled:
                    continue
                value = booking.custom_data.get(field.field_name)
                if value:
                    lines.append(f"{field.display_label}: {value}")

        # Add booking ID for reference
        lines.append(f"Booking ID: {booking.cloudbeds_booking_id}")

        return "\\n".join(lines)

    def _get_timezone(self, timezone_str: str) -> ZoneInfo:
        """Get ZoneInfo for timezone string with fallback to UTC.

        Args:
            timezone_str: IANA timezone identifier.

        Returns:
            ZoneInfo object, defaults to UTC on invalid timezone.
        """
        try:
            return ZoneInfo(timezone_str)
        except ZoneInfoNotFoundError:
            logger.warning("Invalid timezone '%s', falling back to UTC", timezone_str)
            return ZoneInfo("UTC")

    def _to_date_with_tz(self, dt: datetime, tz: ZoneInfo) -> datetime:
        """Convert datetime to timezone-aware date.

        For iCal all-day events, we use the date only.

        Args:
            dt: Input datetime (may be naive or aware).
            tz: Target timezone.

        Returns:
            Timezone-aware datetime.
        """
        if dt.tzinfo is None:
            # Naive datetime - assume it's in the target timezone
            return dt.replace(tzinfo=tz)
        # Already aware - convert to target timezone
        return dt.astimezone(tz)

    def _generate_uid(self, booking: Booking) -> str:
        """Generate unique event ID for booking.

        Args:
            booking: Booking to generate UID for.

        Returns:
            Unique identifier string.
        """
        # Use hash of listing_id + cloudbeds_booking_id for stability
        unique_str = f"{booking.listing_id}-{booking.cloudbeds_booking_id}"
        hash_hex = hashlib.sha256(unique_str.encode()).hexdigest()[:16]
        return f"{hash_hex}@rentalsync-bridge"

    @staticmethod
    def _truncate_summary(summary: str, max_length: int = 255) -> str:
        """Truncate summary to max length for iCal compatibility.

        Args:
            summary: Event summary/title.
            max_length: Maximum length (default 255).

        Returns:
            Truncated summary with ellipsis if needed.
        """
        if len(summary) <= max_length:
            return summary
        return summary[: max_length - 3] + "..."

    @staticmethod
    def extract_phone_last4(phone: str | None) -> str | None:
        """Extract last 4 digits from phone number.

        Args:
            phone: Full phone number string.

        Returns:
            Last 4 digits or None if not enough digits.
        """
        if not phone:
            return None

        # Extract only digits
        digits = "".join(c for c in phone if c.isdigit())

        if len(digits) < MIN_PHONE_DIGITS:
            return None

        return digits[-MIN_PHONE_DIGITS:]
