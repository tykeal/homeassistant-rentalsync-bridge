# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Privacy regression tests for the production DEBUG logging configuration.

Users are asked to enable the add-on's ``debug`` log level and paste the
resulting output into public GitHub issues.  These tests drive a realistic
sync-and-export flow with the *real* ``setup_logging()`` configuration and
assert that no guest personal data and no credential ever reaches the
captured output, regardless of which logger emitted it.
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from io import StringIO
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from src.config import Settings
from src.models.booking import Booking
from src.services.calendar_service import CalendarService
from src.services.cloudbeds_service import CloudbedsService
from src.services.sync_service import SyncService
from src.utils import logging as logging_utils

from tests.conftest import (
    GuestyFakeTransport,
    make_booking,
    make_listing,
    make_mock_provider,
    make_pms_reservation,
    make_room,
)

# Distinctive sentinel values.  None of these may ever appear in log output.
GUEST_NAME = "Zebulon Quartzfeather"
GUEST_EMAIL = "zebulon.quartzfeather@example-sentinel.invalid"
GUEST_PHONE = "+15557770001"
GUEST_ADDRESS = "4417 Sentinel Hollow Road"
DOOR_CODE_VALUE = "door-sentinel-5150"
KEY_CODE_VALUE = "keycode-sentinel-8842"
API_TOKEN = "sentinel-access-token-7f3a9c1e"
CLIENT_SECRET = "sentinel-client-secret-b2d8"

PII_SENTINELS = (
    GUEST_NAME,
    GUEST_EMAIL,
    GUEST_PHONE,
    GUEST_ADDRESS,
    DOOR_CODE_VALUE,
    KEY_CODE_VALUE,
)
CREDENTIAL_SENTINELS = (
    API_TOKEN,
    CLIENT_SECRET,
)


@pytest.fixture
def debug_log_stream(monkeypatch):
    """Configure production logging at DEBUG and capture everything.

    Invokes the real ``setup_logging()`` entry point so the test exercises
    the shipped configuration (including library logger pinning) rather than
    a hand-rolled logger.  The previous root logger state is restored on
    teardown so other tests are unaffected.

    Yields:
        StringIO buffer receiving every record the configuration emits.
    """
    root_logger = logging.getLogger()
    saved_handlers = root_logger.handlers[:]
    saved_level = root_logger.level
    saved_levels = {
        name: logging.getLogger(name).level
        for name in (*logging_utils.SENSITIVE_LOGGERS, "uvicorn", "apscheduler")
    }

    monkeypatch.setattr(
        logging_utils,
        "get_settings",
        lambda: Settings(log_level="debug"),
    )
    monkeypatch.delenv(logging_utils.SQL_DEBUG_ENV_VAR, raising=False)

    stream = StringIO()
    logging_utils.setup_logging(stream=stream)
    assert root_logger.level == logging.DEBUG

    yield stream

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    for handler in saved_handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(saved_level)
    for name, level in saved_levels.items():
        logging.getLogger(name).setLevel(level)


def _assert_no_sentinels(output: str) -> None:
    """Assert no sentinel guest data or credential appears in output.

    Args:
        output: Complete captured log output.
    """
    for sentinel in PII_SENTINELS:
        assert sentinel not in output, f"guest data leaked into logs: {sentinel}"
    for sentinel in CREDENTIAL_SENTINELS:
        assert sentinel not in output, f"credential leaked into logs: {sentinel}"


def _raw_cloudbeds_reservation() -> dict:
    """Build a raw Cloudbeds reservation payload containing guest PII.

    Returns:
        Reservation dict as the Cloudbeds API would return it.
    """
    return {
        "reservationID": "RES_PRIVACY_1",
        "status": "confirmed",
        "startDate": "2026-03-01",
        "endDate": "2026-03-05",
        "guestName": GUEST_NAME,
        "guestEmail": GUEST_EMAIL,
        "guestPhone": GUEST_PHONE,
        "guestAddress": GUEST_ADDRESS,
        "rooms": [{"roomID": "ROOM_PRIVACY"}],
    }


def _install_cloudbeds_client(monkeypatch, payload: dict) -> None:
    """Install a mock httpx client returning the supplied API payload.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        payload: JSON body the mocked API returns.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.json.return_value = payload

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)


class TestProductionDebugLoggingPrivacy:
    """Tests that production DEBUG logging never discloses guest data."""

    @pytest.mark.asyncio
    async def test_end_to_end_sync_and_feed_leak_no_pii(
        self, async_session, debug_log_stream, monkeypatch
    ):
        """Test a full fetch/sync/export cycle emits no PII or credentials."""
        _install_cloudbeds_client(
            monkeypatch,
            {"success": True, "data": [_raw_cloudbeds_reservation()]},
        )
        service = CloudbedsService(
            access_token=API_TOKEN,
            refresh_token=CLIENT_SECRET,
        )
        await service.get_reservations("PROP_PRIVACY")

        listing = make_listing(pms_id="PROP_PRIVACY", slug="prop-privacy")
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        check_in = datetime.now(UTC) + timedelta(days=3)
        provider = make_mock_provider(
            reservations=[
                make_pms_reservation(
                    pms_booking_id="RES_PRIVACY_1",
                    listing_pms_id="PROP_PRIVACY",
                    guest_name=GUEST_NAME,
                    check_in=check_in,
                    check_out=check_in + timedelta(days=4),
                    custom_data={
                        "guest_email": GUEST_EMAIL,
                        "guest_phone": GUEST_PHONE,
                        "guest_address": GUEST_ADDRESS,
                    },
                )
            ],
        )

        sync_service = SyncService(async_session)
        counts = await sync_service.sync_listing(listing, provider)
        assert counts["inserted"] == 1

        bookings = list(
            (
                await async_session.execute(
                    Booking.__table__.select().where(
                        Booking.__table__.c.listing_id == listing.id
                    )
                )
            ).all()
        )
        assert bookings

        ical = CalendarService().generate_ical(
            listing,
            [
                Booking(
                    listing_id=listing.id,
                    pms_booking_id="RES_PRIVACY_1",
                    guest_name=GUEST_NAME,
                    check_in_date=check_in,
                    check_out_date=check_in + timedelta(days=4),
                    status="confirmed",
                )
            ],
        )
        assert "BEGIN:VCALENDAR" in ical

        output = debug_log_stream.getvalue()
        # Sanity check: the configuration really is emitting DEBUG records.
        assert "DEBUG" in output
        _assert_no_sentinels(output)

    @pytest.mark.asyncio
    async def test_sql_statements_and_parameters_are_not_logged(
        self, async_session, debug_log_stream
    ):
        """Test database loggers stay quiet under the add-on debug level."""
        listing = make_listing(pms_id="PROP_SQL", slug="prop-sql")
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

        async_session.add(
            Booking(
                listing_id=listing.id,
                pms_booking_id="RES_SQL",
                guest_name=GUEST_NAME,
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=3),
                status="confirmed",
            )
        )
        await async_session.commit()

        output = debug_log_stream.getvalue()
        _assert_no_sentinels(output)
        assert "INSERT INTO" not in output
        assert "SELECT" not in output

    def test_sensitive_library_loggers_are_pinned(self, debug_log_stream):
        """Test sensitive loggers never drop below the pinned level."""
        for name in logging_utils.SENSITIVE_LOGGERS:
            library_logger = logging.getLogger(name)
            assert library_logger.getEffectiveLevel() >= logging.WARNING
            assert not library_logger.isEnabledFor(logging.DEBUG)
            assert not library_logger.isEnabledFor(logging.INFO)


class TestGuestyV3DebugDiagnostics:
    """V3 DEBUG logs answer research R15 without PII (T066, FR-043)."""

    @pytest.mark.asyncio
    async def test_v3_cycle_logs_diagnostics_without_pii(
        self,
        async_session,
        async_session_factory,
        debug_log_stream,
        guesty_transport: GuestyFakeTransport,
    ):
        """Test every R15 diagnostic is logged and no guest data is."""
        p = guesty_transport.payloads
        single, parent = p.new_id("lst"), p.new_id("lst")
        unit_a, unit_b, untracked = p.new_id("lst"), p.new_id("lst"), p.new_id("lst")
        guesty_transport.add_listing(single)
        guesty_transport.add_listing(parent, children=[unit_a, unit_b])
        guesty_transport.add_listing(untracked)
        listings = {}
        for pms_id, rooms in ((single, [single]), (parent, [unit_a, unit_b])):
            listing = make_listing(pms_id=pms_id, slug=f"pv-{pms_id[-6:]}")
            async_session.add(listing)
            await async_session.commit()
            await async_session.refresh(listing)
            for room in rooms:
                async_session.add(
                    make_room(
                        listing_id=listing.id,
                        pms_room_id=room,
                        slug=f"pv-{room[-6:]}",
                    )
                )
            await async_session.commit()
            listings[pms_id] = listing

        notes = {"other": "", "guest": "", "keyCode": KEY_CODE_VALUE}
        stay = p.search_item(
            single,
            notes=notes,
            custom_fields=p.custom_fields({"door_code": DOOR_CODE_VALUE}),
        )
        trip = p.round_trip_move(parent, unit_a, unit_b, notes=notes)
        moved_id = p.new_id("res")
        moved = p.rearranged_into_untracked(
            untracked, single, start=0, reservation_id=moved_id, notes=notes
        )
        guesty_transport.v3_items = [stay, trip, moved]
        for item in guesty_transport.v3_items:
            guesty_transport.guests[item["guestId"]] = p.guests_crud_body(
                item["guestId"],
                full_name=GUEST_NAME,
                phone=GUEST_PHONE,
                email=GUEST_EMAIL,
            )
        for rid, code, start, end in (
            (stay["reservationId"], stay["confirmationCode"], 7, 10),
            (moved_id, moved["confirmationCode"], 2, 5),
        ):
            async_session.add(
                make_booking(
                    listing_id=listings[single].id,
                    pms_booking_id=f"{rid}::{single}",
                    guest_name=GUEST_NAME,
                    check_in_date=p.check_in_at(start),
                    check_out_date=p.check_out_at(end),
                    custom_data={"confirmationCode": code},
                )
            )
        await async_session.commit()

        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([single, parent])
        service = SyncService(async_session, session_factory=async_session_factory)
        for listing in listings.values():
            await service.sync_listing(listing, provider)
        await provider.aclose()

        output = debug_log_stream.getvalue()
        _assert_no_sentinels(output)
        trip_id = trip["reservationId"]
        expected = [
            # Filter IDs and chunks.
            r"Guesty V3 search: filter_ids=\d+ chunks=1 window=",
            # Per-chunk pagination signals.
            r"Guesty V3 search chunk 1/1: listing_ids=\d+ pages=1 "
            r"has_more=\[False\] items=2",
            # Totals, cross-chunk duplicates, status and segment histograms.
            r"Guesty V3 search result: items=2 duplicates=0 "
            r"statuses=\{'confirmed': 2\} segments=\{1: 1, 3: 1\}",
            # Every segment's listing ID per reservation.
            rf"Guesty V3 reservation {trip_id}: status=confirmed segments=3 "
            rf"listings={unit_a},{unit_b},{unit_a}",
            # Per-listing ownership and window narrowing.
            rf"Guesty V3 listing {parent}: owned=1 in_window=1 intervals=3 "
            r"extra_intervals=2 holds=0 unclassified=0",
            # Matched-ID count.
            rf"Identity check for listing {single}: 1 stored reservation\(s\) "
            r"matched by ID",
            # FR-011a candidates, verdicts and control outcome.
            rf"Absence check for listing {single}: candidates=1 "
            r"verdicts=\{'reattributed': 1\}",
            rf"Guesty V3 by-IDs lookup for listing {single}: candidates=1 "
            r"control=returned",
            rf"INFO.*Guesty V3 reservation {moved_id} re-attributed: "
            rf"segment listings {untracked},{single}",
            # Per-cycle request counts and enrichment reuse.
            r"INFO.*Guesty requests this cycle: search=1 by_ids=1",
            r"Enrichment for listing \S+: reused=\d+ fetched=\d+",
        ]
        missing = [line for line in expected if not re.search(line, output)]
        assert not missing, f"diagnostics missing: {missing}"
