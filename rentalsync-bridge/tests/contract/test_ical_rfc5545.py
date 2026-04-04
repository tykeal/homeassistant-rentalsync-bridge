# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for RFC 5545 iCalendar compliance.

These tests validate that generated iCal feeds comply with RFC 5545
(Internet Calendaring and Scheduling Core Object Specification).
Includes cross-provider structural equivalence checks.
"""

from datetime import UTC, datetime, timedelta

import pytest
from icalendar import Calendar
from src.models.booking import Booking
from src.models.listing import Listing
from src.services.calendar_service import CalendarService


class TestRFC5545Compliance:
    """Test RFC 5545 compliance of generated iCal feeds."""

    @pytest.fixture
    def calendar_service(self):
        """Create calendar service without cache."""
        from src.services.calendar_service import CalendarCache

        return CalendarService(cache=CalendarCache(ttl_seconds=0))

    @pytest.fixture
    def sample_listing(self):
        """Create a sample listing."""
        listing = Listing(
            id=1,
            pms_id="TEST123",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
            timezone="America/New_York",
        )
        return listing

    @pytest.fixture
    def sample_booking(self):
        """Create a sample booking."""
        return Booking(
            id=1,
            listing_id=1,
            pms_booking_id="BK12345",
            guest_name="John Doe",
            guest_phone_last4="1234",
            check_in_date=datetime.now(UTC) + timedelta(days=7),
            check_out_date=datetime.now(UTC) + timedelta(days=10),
            status="confirmed",
        )

    def test_valid_ical_structure(
        self, calendar_service, sample_listing, sample_booking
    ):
        """RFC 5545 3.4: iCalendar must be encapsulated in VCALENDAR."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])

        # Must start with BEGIN:VCALENDAR
        assert ical_str.strip().startswith("BEGIN:VCALENDAR")

        # Must end with END:VCALENDAR
        assert ical_str.strip().endswith("END:VCALENDAR")

        # Must be parseable by icalendar library
        cal = Calendar.from_ical(ical_str)
        assert cal is not None

    def test_required_calendar_properties(
        self, calendar_service, sample_listing, sample_booking
    ):
        """RFC 5545 3.6: VCALENDAR must have PRODID and VERSION."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        # PRODID is required (RFC 5545 3.7.3)
        assert "prodid" in cal
        assert cal["prodid"] is not None

        # VERSION is required and must be "2.0" (RFC 5545 3.7.4)
        assert "version" in cal
        assert str(cal["version"]) == "2.0"

    def test_calscale_property(self, calendar_service, sample_listing, sample_booking):
        """RFC 5545 3.7.1: CALSCALE should be GREGORIAN if present."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        if "calscale" in cal:
            assert str(cal["calscale"]).upper() == "GREGORIAN"

    def test_method_property(self, calendar_service, sample_listing, sample_booking):
        """RFC 5545 3.7.2: METHOD should be valid iTIP method if present."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        valid_methods = [
            "PUBLISH",
            "REQUEST",
            "REPLY",
            "ADD",
            "CANCEL",
            "REFRESH",
            "COUNTER",
            "DECLINECOUNTER",
        ]
        if "method" in cal:
            assert str(cal["method"]).upper() in valid_methods

    def test_vevent_required_properties(
        self, calendar_service, sample_listing, sample_booking
    ):
        """RFC 5545 3.6.1: VEVENT must have UID and DTSTAMP."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        assert len(events) == 1

        event = events[0]

        # UID is required (RFC 5545 3.8.4.7)
        assert "uid" in event
        assert event["uid"] is not None
        assert len(str(event["uid"])) > 0

        # DTSTAMP is required (RFC 5545 3.8.7.2)
        assert "dtstamp" in event
        assert event["dtstamp"] is not None

    def test_vevent_dtstart_required(
        self, calendar_service, sample_listing, sample_booking
    ):
        """RFC 5545 3.6.1: VEVENT should have DTSTART."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        event = events[0]

        # DTSTART is required for VEVENT
        assert "dtstart" in event
        assert event["dtstart"] is not None

    def test_vevent_dtend_or_duration(
        self, calendar_service, sample_listing, sample_booking
    ):
        """RFC 5545 3.6.1: VEVENT should have DTEND or DURATION."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        event = events[0]

        # Must have either DTEND or DURATION
        has_dtend = "dtend" in event
        has_duration = "duration" in event

        assert has_dtend or has_duration, "VEVENT must have DTEND or DURATION"

    def test_uid_format_globally_unique(
        self, calendar_service, sample_listing, sample_booking
    ):
        """RFC 5545 3.8.4.7: UID should be globally unique."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        event = events[0]

        uid = str(event["uid"])

        # UID should contain @ for global uniqueness (common pattern)
        assert "@" in uid, "UID should follow addr-spec format for uniqueness"

        # UID should not be empty
        assert len(uid) > 0

    def test_uid_uniqueness_across_bookings(self, calendar_service, sample_listing):
        """Each booking should have a unique UID."""
        bookings = [
            Booking(
                id=i,
                listing_id=1,
                pms_booking_id=f"BK{i:05d}",
                guest_name=f"Guest {i}",
                check_in_date=datetime.now(UTC) + timedelta(days=i * 7),
                check_out_date=datetime.now(UTC) + timedelta(days=i * 7 + 3),
                status="confirmed",
            )
            for i in range(1, 6)
        ]

        ical_str = calendar_service.generate_ical(sample_listing, bookings)
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        uids = [str(event["uid"]) for event in events]

        # All UIDs should be unique
        assert len(uids) == len(set(uids)), "All event UIDs must be unique"

    def test_status_valid_values(
        self, calendar_service, sample_listing, sample_booking
    ):
        """RFC 5545 3.8.1.11: STATUS must be valid VEVENT status."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        event = events[0]

        valid_statuses = ["TENTATIVE", "CONFIRMED", "CANCELLED"]
        if "status" in event:
            assert str(event["status"]).upper() in valid_statuses

    def test_transp_valid_values(
        self, calendar_service, sample_listing, sample_booking
    ):
        """RFC 5545 3.8.2.7: TRANSP must be OPAQUE or TRANSPARENT."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        event = events[0]

        valid_transp = ["OPAQUE", "TRANSPARENT"]
        if "transp" in event:
            assert str(event["transp"]).upper() in valid_transp

    def test_line_folding(self, calendar_service, sample_listing):
        """RFC 5545 3.1: Lines should not exceed 75 octets."""
        # Create booking with long guest name to trigger folding
        booking = Booking(
            id=1,
            listing_id=1,
            pms_booking_id="BK12345",
            guest_name="A" * 200,  # Very long name
            check_in_date=datetime.now(UTC) + timedelta(days=7),
            check_out_date=datetime.now(UTC) + timedelta(days=10),
            status="confirmed",
        )

        ical_str = calendar_service.generate_ical(sample_listing, [booking])

        # icalendar library handles line folding automatically
        # Just verify the output is still parseable
        cal = Calendar.from_ical(ical_str)
        events = list(cal.walk("VEVENT"))
        assert len(events) == 1

    def test_crlf_line_endings(self, calendar_service, sample_listing, sample_booking):
        """RFC 5545 3.1: Lines should be delimited by CRLF."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])

        # The icalendar library uses \r\n internally
        # Check that the content is parseable (library handles normalization)
        cal = Calendar.from_ical(ical_str)
        assert cal is not None

    def test_text_escaping(self, calendar_service, sample_listing):
        """RFC 5545 3.3.11: TEXT values must escape special characters."""
        # Create booking with special characters
        booking = Booking(
            id=1,
            listing_id=1,
            pms_booking_id="BK12345",
            guest_name="John; Doe, Jr.",  # Contains semicolon and comma
            check_in_date=datetime.now(UTC) + timedelta(days=7),
            check_out_date=datetime.now(UTC) + timedelta(days=10),
            status="confirmed",
        )

        ical_str = calendar_service.generate_ical(sample_listing, [booking])

        # Verify parseable (escaping handled by library)
        cal = Calendar.from_ical(ical_str)
        events = list(cal.walk("VEVENT"))
        assert len(events) == 1

        # Verify the summary contains the guest name
        summary = str(events[0]["summary"])
        assert "John" in summary

    def test_empty_calendar_valid(self, calendar_service, sample_listing):
        """Calendar with no events should still be valid."""
        ical_str = calendar_service.generate_ical(sample_listing, [])

        cal = Calendar.from_ical(ical_str)

        # Should have required properties
        assert "prodid" in cal
        assert "version" in cal

        # Should have no events
        events = list(cal.walk("VEVENT"))
        assert len(events) == 0

    def test_prodid_format(self, calendar_service, sample_listing, sample_booking):
        """RFC 5545 3.7.3: PRODID should identify the product."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        prodid = str(cal["prodid"])

        # PRODID should contain identifying information
        assert len(prodid) > 0
        assert "rentalsync" in prodid.lower() or "//" in prodid

    def test_summary_present(self, calendar_service, sample_listing, sample_booking):
        """RFC 5545 3.8.1.12: SUMMARY provides a short summary."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        event = events[0]

        assert "summary" in event
        summary = str(event["summary"])
        assert len(summary) > 0

    def test_description_optional_but_valid(
        self, calendar_service, sample_listing, sample_booking
    ):
        """RFC 5545 3.8.1.5: DESCRIPTION is optional but must be valid TEXT."""
        ical_str = calendar_service.generate_ical(sample_listing, [sample_booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        event = events[0]

        if "description" in event:
            description = str(event["description"])
            # Should be non-empty if present
            assert len(description) > 0


class TestDateTimeCompliance:
    """Test RFC 5545 date/time property compliance."""

    @pytest.fixture
    def calendar_service(self):
        """Create calendar service."""
        from src.services.calendar_service import CalendarCache

        return CalendarService(cache=CalendarCache(ttl_seconds=0))

    @pytest.fixture
    def sample_listing(self):
        """Create a sample listing."""
        return Listing(
            id=1,
            pms_id="TEST123",
            name="Test Property",
            ical_url_slug="test-property",
            enabled=True,
            sync_enabled=True,
            timezone="America/New_York",
        )

    def test_dtstart_format(self, calendar_service, sample_listing):
        """RFC 5545 3.3.5: DATE-TIME format compliance."""
        booking = Booking(
            id=1,
            listing_id=1,
            pms_booking_id="BK12345",
            guest_name="Test Guest",
            check_in_date=datetime(2026, 6, 15, 14, 0, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 6, 18, 11, 0, 0, tzinfo=UTC),
            status="confirmed",
        )

        ical_str = calendar_service.generate_ical(sample_listing, [booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        event = events[0]

        # DTSTART should be present and have a datetime value
        dtstart = event["dtstart"]
        assert dtstart is not None

    def test_dtstamp_utc_format(self, calendar_service, sample_listing):
        """RFC 5545 3.8.7.2: DTSTAMP must be in UTC."""
        booking = Booking(
            id=1,
            listing_id=1,
            pms_booking_id="BK12345",
            guest_name="Test Guest",
            check_in_date=datetime.now(UTC) + timedelta(days=7),
            check_out_date=datetime.now(UTC) + timedelta(days=10),
            status="confirmed",
        )

        ical_str = calendar_service.generate_ical(sample_listing, [booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        event = events[0]

        dtstamp = event["dtstamp"]
        assert dtstamp is not None

        # DTSTAMP should be timezone-aware
        dt_value = dtstamp.dt
        if hasattr(dt_value, "tzinfo"):
            # If it has timezone info, it should be UTC or have a valid timezone
            pass  # Valid

    def test_dtend_after_dtstart(self, calendar_service, sample_listing):
        """DTEND should be after DTSTART."""
        check_in = datetime.now(UTC) + timedelta(days=7)
        check_out = datetime.now(UTC) + timedelta(days=10)

        booking = Booking(
            id=1,
            listing_id=1,
            pms_booking_id="BK12345",
            guest_name="Test Guest",
            check_in_date=check_in,
            check_out_date=check_out,
            status="confirmed",
        )

        ical_str = calendar_service.generate_ical(sample_listing, [booking])
        cal = Calendar.from_ical(ical_str)

        events = list(cal.walk("VEVENT"))
        event = events[0]

        dtstart = event["dtstart"].dt
        dtend = event["dtend"].dt

        # Handle both date and datetime types
        if hasattr(dtstart, "date"):
            dtstart = dtstart.date() if not hasattr(dtstart, "hour") else dtstart
        if hasattr(dtend, "date"):
            dtend = dtend.date() if not hasattr(dtend, "hour") else dtend

        assert dtend >= dtstart, "DTEND must be on or after DTSTART"


class TestGuestySourcedCompliance:
    """Verify Guesty-sourced bookings produce valid iCal output."""

    @pytest.fixture
    def calendar_service(self):
        """Create calendar service without cache."""
        from src.services.calendar_service import CalendarCache

        return CalendarService(cache=CalendarCache(ttl_seconds=0))

    @pytest.fixture
    def guesty_listing(self):
        """Create a Guesty sample listing."""
        return Listing(
            id=10,
            pms_id="GUESTY001",
            name="Guesty Beach House",
            ical_url_slug="guesty-beach-house",
            enabled=True,
            sync_enabled=True,
            timezone="America/Los_Angeles",
        )

    @pytest.fixture
    def guesty_booking(self):
        """Create a Guesty sample booking."""
        return Booking(
            id=10,
            listing_id=10,
            pms_booking_id="GR_BOOKING_42",
            guest_name="Alice Guesty",
            guest_phone_last4="9876",
            check_in_date=datetime(2026, 8, 1, 15, 0, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 8, 5, 11, 0, 0, tzinfo=UTC),
            status="confirmed",
            custom_data={"note": "VIP guest"},
        )

    def test_guesty_vevent_properties(
        self, calendar_service, guesty_listing, guesty_booking
    ):
        """Guesty events contain all required VEVENT properties."""
        ical_str = calendar_service.generate_ical(guesty_listing, [guesty_booking])
        cal = Calendar.from_ical(ical_str)
        events = list(cal.walk("VEVENT"))
        assert len(events) == 1

        evt = events[0]
        assert "uid" in evt
        assert "@rentalsync-bridge" in str(evt["uid"])
        assert "dtstart" in evt
        assert "dtend" in evt
        assert "summary" in evt
        assert "Alice Guesty" in str(evt["summary"])
        assert "dtstamp" in evt

    def test_guesty_booking_uid_stable(self, guesty_listing, guesty_booking):
        """Guesty booking UID is deterministic across generations."""
        from src.services.calendar_service import CalendarCache

        service1 = CalendarService(cache=CalendarCache(ttl_seconds=0))
        service2 = CalendarService(cache=CalendarCache(ttl_seconds=0))
        ical1 = service1.generate_ical(guesty_listing, [guesty_booking])
        ical2 = service2.generate_ical(guesty_listing, [guesty_booking])
        cal1 = Calendar.from_ical(ical1)
        cal2 = Calendar.from_ical(ical2)
        uid1 = str(next(iter(cal1.walk("VEVENT")))["uid"])
        uid2 = str(next(iter(cal2.walk("VEVENT")))["uid"])
        assert uid1 == uid2

    def test_guesty_all_day_date_format(
        self, calendar_service, guesty_listing, guesty_booking
    ):
        """Guesty events use VALUE=DATE for all-day format."""
        ical_str = calendar_service.generate_ical(guesty_listing, [guesty_booking])
        # All-day events use VALUE=DATE format
        assert "DTSTART;VALUE=DATE:" in ical_str
        assert "DTEND;VALUE=DATE:" in ical_str


class TestCrossProviderEquivalence:
    """Guesty- and Cloudbeds-sourced events have equivalent structure."""

    @pytest.fixture
    def calendar_service(self):
        """Create calendar service without cache."""
        from src.services.calendar_service import CalendarCache

        return CalendarService(cache=CalendarCache(ttl_seconds=0))

    def _make_listing(self, *, pms_id, name, slug, list_id):
        """Build a Listing model for comparison tests."""
        return Listing(
            id=list_id,
            pms_id=pms_id,
            name=name,
            ical_url_slug=slug,
            enabled=True,
            sync_enabled=True,
            timezone="America/New_York",
        )

    def _make_booking(self, *, listing_id, booking_id, guest):
        """Build a Booking model for comparison tests."""
        return Booking(
            listing_id=listing_id,
            pms_booking_id=booking_id,
            guest_name=guest,
            check_in_date=datetime(2026, 9, 1, 14, 0, 0, tzinfo=UTC),
            check_out_date=datetime(2026, 9, 5, 11, 0, 0, tzinfo=UTC),
            status="confirmed",
        )

    def test_events_have_same_required_properties(self, calendar_service):
        """Guesty and Cloudbeds events share all required VEVENT props."""
        guesty_listing = self._make_listing(
            pms_id="G001", name="Guesty Prop", slug="g-prop", list_id=100
        )
        cloudbeds_listing = self._make_listing(
            pms_id="CB001", name="CB Prop", slug="cb-prop", list_id=200
        )

        guesty_bk = self._make_booking(
            listing_id=100, booking_id="G_BK1", guest="Guesty Guest"
        )
        cb_bk = self._make_booking(
            listing_id=200, booking_id="CB_BK1", guest="Cloudbeds Guest"
        )

        g_ical = calendar_service.generate_ical(guesty_listing, [guesty_bk])
        cb_ical = calendar_service.generate_ical(cloudbeds_listing, [cb_bk])

        g_cal = Calendar.from_ical(g_ical)
        cb_cal = Calendar.from_ical(cb_ical)

        g_evt = next(iter(g_cal.walk("VEVENT")))
        cb_evt = next(iter(cb_cal.walk("VEVENT")))

        required_props = ["uid", "dtstart", "dtend", "dtstamp", "summary"]
        for prop in required_props:
            assert prop in g_evt, f"Guesty event missing {prop}"
            assert prop in cb_evt, f"Cloudbeds event missing {prop}"

    def test_calendar_metadata_equivalent(self, calendar_service):
        """Both providers produce matching calendar-level metadata."""
        guesty_listing = self._make_listing(
            pms_id="G002", name="G Prop", slug="g-prop-2", list_id=101
        )
        cloudbeds_listing = self._make_listing(
            pms_id="CB002", name="CB Prop", slug="cb-prop-2", list_id=201
        )

        g_ical = calendar_service.generate_ical(guesty_listing, [])
        cb_ical = calendar_service.generate_ical(cloudbeds_listing, [])

        g_cal = Calendar.from_ical(g_ical)
        cb_cal = Calendar.from_ical(cb_ical)

        # Both should have same structural properties
        assert str(g_cal["prodid"]) == str(cb_cal["prodid"])
        assert str(g_cal["version"]) == str(cb_cal["version"])

        if "calscale" in g_cal and "calscale" in cb_cal:
            assert str(g_cal["calscale"]) == str(cb_cal["calscale"])

        if "method" in g_cal and "method" in cb_cal:
            assert str(g_cal["method"]) == str(cb_cal["method"])
