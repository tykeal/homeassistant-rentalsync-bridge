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

from tests.conftest import make_listing, make_mock_provider, make_pms_reservation

# Distinctive sentinel values.  None of these may ever appear in log output.
GUEST_NAME = "Zebulon Quartzfeather"
GUEST_EMAIL = "zebulon.quartzfeather@example-sentinel.invalid"
GUEST_PHONE = "+15557770001"
GUEST_ADDRESS = "4417 Sentinel Hollow Road"
API_TOKEN = "sentinel-access-token-7f3a9c1e"
CLIENT_SECRET = "sentinel-client-secret-b2d8"

PII_SENTINELS = (
    GUEST_NAME,
    GUEST_EMAIL,
    GUEST_PHONE,
    GUEST_ADDRESS,
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
