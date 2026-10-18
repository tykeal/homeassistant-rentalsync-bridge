# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for in-progress reservations (issues #99 / #111).

Drives the real pipeline -- provider HTTP response -> provider mapper ->
``SyncService`` -> ``BookingRepository`` -> ``CalendarService`` -- and
asserts on the parsed ``.ics`` output.  A stay that started before today and
ends after it must never disappear from the exported feed.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from icalendar import Calendar
from src.providers.cloudbeds.provider import CloudbedsProvider
from src.providers.guesty.service import GUESTY_BASE_URL, GuestyProvider
from src.repositories.booking_repository import BookingRepository
from src.services.calendar_service import CalendarCache, CalendarService
from src.services.sync_service import SyncService

from tests.conftest import make_listing, make_room

LISTING_PMS_ID = "PROP001"
ROOM_PMS_ID = "ROOM001"

# (booking id, check-in offset days, check-out offset days, expected in feed)
SCENARIOS = (
    ("A-INPROGRESS", -2, 3, True),
    ("B-FUTURE", 10, 14, True),
    ("C-RECENT-PAST", -6, -3, True),
    ("D-LONG-PAST", -124, -120, False),
)


def _iso(now: datetime, days: int, hour: int | None) -> str:
    """Build a provider date/datetime string.

    Args:
        now: Reference instant.
        days: Day offset from *now*.
        hour: UTC hour for timed stays, or None for date-only.

    Returns:
        ISO-format string as a PMS would supply it.
    """
    moment = now + timedelta(days=days)
    if hour is None:
        return moment.strftime("%Y-%m-%d")
    return moment.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def _guesty_payload(booking_id, check_in, check_out, status):
    """Build a raw Guesty reservation dict.

    Args:
        booking_id: Reservation ID.
        check_in: Check-in string.
        check_out: Check-out string.
        status: Raw Guesty status.

    Returns:
        Raw reservation dict.
    """
    return {
        "_id": booking_id,
        "status": status,
        "checkIn": check_in,
        "checkOut": check_out,
        "listingId": ROOM_PMS_ID,
        "guest": {"_id": f"g-{booking_id}", "fullName": f"Guest {booking_id}"},
    }


def _cloudbeds_payload(booking_id, check_in, check_out, status):
    """Build a raw Cloudbeds reservation dict.

    Args:
        booking_id: Reservation ID.
        check_in: Check-in string.
        check_out: Check-out string.
        status: Raw Cloudbeds status.

    Returns:
        Raw reservation dict.
    """
    return {
        "reservationID": booking_id,
        "status": status,
        "startDate": check_in,
        "endDate": check_out,
        "guestName": f"Guest {booking_id}",
        "rooms": [{"roomID": ROOM_PMS_ID}],
    }


def _raw_scenarios(provider_name, now, status, hour_pair=(None, None)):
    """Build the raw provider payloads for every scenario.

    Args:
        provider_name: "guesty" or "cloudbeds".
        now: Reference instant.
        status: Raw status for the in-progress stay.
        hour_pair: (check-in hour, check-out hour) or (None, None).

    Returns:
        List of raw reservation dicts.
    """
    in_hour, out_hour = hour_pair
    builder = _guesty_payload if provider_name == "guesty" else _cloudbeds_payload
    return [
        builder(
            booking_id,
            _iso(now, start, in_hour),
            _iso(now, end, out_hour),
            status if booking_id == "A-INPROGRESS" else "confirmed",
        )
        for booking_id, start, end, _expected in SCENARIOS
    ]


def _map_all(provider_name, raw_items):
    """Map raw payloads through the real provider mappers.

    Args:
        provider_name: "guesty" or "cloudbeds".
        raw_items: Raw reservation dicts.

    Returns:
        List of PMSReservation DTOs.
    """
    mapper = (
        GuestyProvider._map_reservation
        if provider_name == "guesty"
        else CloudbedsProvider._map_reservation
    )
    mapped = [mapper(item, LISTING_PMS_ID) for item in raw_items]
    assert all(dto is not None for dto in mapped)
    return mapped


async def _setup(session):
    """Create a listing and room in a non-UTC timezone.

    Args:
        session: Async database session.

    Returns:
        Tuple of (listing, room).
    """
    listing = make_listing(
        pms_id=LISTING_PMS_ID,
        name="In-progress Property",
        slug="in-progress",
        timezone="America/New_York",
    )
    session.add(listing)
    await session.commit()
    await session.refresh(listing)

    room = make_room(
        listing_id=listing.id,
        pms_room_id=ROOM_PMS_ID,
        room_name="Main Room",
        slug="main-room",
    )
    session.add(room)
    await session.commit()
    await session.refresh(room)
    return listing, room


async def _feed_summaries(session, listing, room_id):
    """Generate the room iCal feed and return the event summaries.

    Args:
        session: Async database session.
        listing: Listing to export.
        room_id: Room ID for the room-level feed.

    Returns:
        Set of event SUMMARY values in the generated feed.
    """
    bookings = await BookingRepository(session).get_confirmed_for_listing(
        listing.id, room_id=room_id
    )
    service = CalendarService(cache=CalendarCache(ttl_seconds=0))
    ics = service.generate_ical(
        listing=listing,
        bookings=list(bookings),
        custom_fields=[],
        room_slug="main-room",
    )
    return {str(c.get("summary")) for c in Calendar.from_ical(ics).walk("VEVENT")}


def _assert_scenarios(summaries):
    """Assert each scenario booking is present or absent as expected.

    Args:
        summaries: Set of feed SUMMARY values.
    """
    for booking_id, _start, _end, expected in SCENARIOS:
        present = any(booking_id in s for s in summaries)
        assert present is expected, (
            f"{booking_id} present={present} expected={expected}; {summaries}"
        )


async def _sync(session, session_factory, listing, provider):
    """Run a sync for *listing* through *provider*.

    Args:
        session: Async database session.
        session_factory: Session factory for error persistence.
        listing: Listing to sync.
        provider: PMS provider (real or mocked).
    """
    sync = SyncService(
        session,
        calendar_cache=CalendarCache(ttl_seconds=0),
        session_factory=session_factory,
    )
    await sync.sync_listing(listing, provider)


def _fake_provider(provider_name, reservations):
    """Build a mock provider returning *reservations*.

    Args:
        provider_name: Provider type string.
        reservations: PMSReservation DTOs to return.

    Returns:
        AsyncMock provider.
    """
    provider = AsyncMock()
    provider.provider_type = provider_name
    provider.has_separate_custom_fields = False
    provider.get_reservations = AsyncMock(return_value=reservations)
    provider.get_guest = AsyncMock(return_value=None)
    provider.get_custom_fields = AsyncMock(return_value={})
    return provider


@pytest.mark.parametrize("provider_name", ["guesty", "cloudbeds"])
@pytest.mark.parametrize("in_progress_status", ["confirmed", "checked_in"])
@pytest.mark.parametrize("hour_pair", [(None, None), (16, 10)], ids=["allday", "timed"])
async def test_in_progress_stay_appears_in_feed(
    async_session,
    async_session_factory,
    provider_name,
    in_progress_status,
    hour_pair,
):
    """An in-progress stay must appear in the exported room feed."""
    now = datetime.now(UTC)
    listing, room = await _setup(async_session)

    reservations = _map_all(
        provider_name, _raw_scenarios(provider_name, now, in_progress_status, hour_pair)
    )
    await _sync(
        async_session,
        async_session_factory,
        listing,
        _fake_provider(provider_name, reservations),
    )

    _assert_scenarios(await _feed_summaries(async_session, listing, room.id))


@pytest.mark.parametrize("provider_name", ["guesty", "cloudbeds"])
async def test_in_progress_stay_survives_absence_from_later_fetch(
    async_session, async_session_factory, provider_name
):
    """The prune guard must retain an in-progress stay missing from a fetch."""
    now = datetime.now(UTC)
    listing, room = await _setup(async_session)

    reservations = _map_all(
        provider_name, _raw_scenarios(provider_name, now, "confirmed")
    )
    provider = _fake_provider(provider_name, reservations)
    await _sync(async_session, async_session_factory, listing, provider)
    await _sync(async_session, async_session_factory, listing, provider)

    assert any(
        "A-INPROGRESS" in s
        for s in await _feed_summaries(async_session, listing, room.id)
    )

    provider.get_reservations = AsyncMock(
        return_value=[r for r in reservations if r.pms_booking_id != "A-INPROGRESS"]
    )
    await _sync(async_session, async_session_factory, listing, provider)

    summaries = await _feed_summaries(async_session, listing, room.id)
    assert any("A-INPROGRESS" in s for s in summaries), summaries


async def test_guesty_http_pipeline(async_session, async_session_factory):
    """Guesty: the in-progress stay survives the real HTTP + mapping path."""
    now = datetime.now(UTC)
    listing, room = await _setup(async_session)
    results = _raw_scenarios("guesty", now, "checked_in")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve a single count-terminated page of Guesty reservations.

        Args:
            request: Outgoing request.

        Returns:
            Mocked JSON response.
        """
        captured.append(request)
        return httpx.Response(200, json={"results": results, "count": len(results)})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=GUESTY_BASE_URL,
    )
    token_manager = AsyncMock()
    token_manager.get_token = AsyncMock(return_value="tok")
    provider = GuestyProvider(token_manager=token_manager, http_client=client)
    provider.get_custom_fields = AsyncMock(return_value={})  # type: ignore[method-assign]

    await _sync(async_session, async_session_factory, listing, provider)
    await provider.aclose()

    params = captured[0].url.params
    assert params["listingId"] == LISTING_PMS_ID
    # Undocumented stay-date filters must not be sent; the window is applied
    # client-side over the fetched superset instead.
    assert "checkIn" not in params
    assert "checkOut" not in params

    _assert_scenarios(await _feed_summaries(async_session, listing, room.id))


async def test_cloudbeds_http_pipeline(async_session, async_session_factory):
    """Cloudbeds: the in-progress stay survives the real HTTP + mapping path."""
    now = datetime.now(UTC)
    listing, room = await _setup(async_session)
    data = _raw_scenarios("cloudbeds", now, "checked_in")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve a single short page of Cloudbeds reservations.

        Args:
            request: Outgoing request.

        Returns:
            Mocked JSON response.
        """
        captured.append(request)
        return httpx.Response(200, json={"success": True, "data": data})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        """Build an AsyncClient bound to the mock transport.

        Returns:
            Patched httpx.AsyncClient.
        """
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    provider = CloudbedsProvider(access_token="tok")
    provider.get_custom_fields = AsyncMock(return_value={})  # type: ignore[method-assign]

    with patch("src.services.cloudbeds_service.httpx.AsyncClient", patched):
        await _sync(async_session, async_session_factory, listing, provider)

    params = captured[0].url.params
    lookback = (
        now - datetime.strptime(params["checkInFrom"], "%Y-%m-%d").replace(tzinfo=UTC)
    ).days
    assert lookback > 300, f"lookback only {lookback} days"
    # ``status`` is a single-value enum in the v1.3 spec and must be omitted.
    assert "status" not in params

    _assert_scenarios(await _feed_summaries(async_session, listing, room.id))
