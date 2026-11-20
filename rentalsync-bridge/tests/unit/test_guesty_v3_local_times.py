# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""V3 stay times come from listing-local dates and listing times (#212).

Reservations V3 reports ``checkIn``/``checkOut`` as the listing's local
wall-clock time labelled ``Z``; a 16:00 America/New_York check-in arrives
as ``T16:00:00.000Z``.  The instants are built from the localized dates,
the listing's ``defaultCheckInTime``/``defaultCheckOutTime`` and its
``timezone`` instead.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from src.providers.base import PMSProviderError
from src.providers.guesty.v3_mapping import map_search_item
from src.utils.stay_time import has_meaningful_times

from tests.conftest import GuestyFakeTransport, GuestyV3Payloads

NY = "America/New_York"


def _wall(day: date, hour: int) -> datetime:
    """Return *day* at *hour* labelled UTC, as V3 reports local times."""
    return datetime.combine(day, time(hour), tzinfo=UTC)


def _local(day: date, hour: int, tz: str = NY) -> datetime:
    """Return the UTC instant of *day* at *hour* in *tz*."""
    return datetime.combine(day, time(hour), tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _midnight(day: date) -> datetime:
    """Return the all-day sentinel for *day* (midnight UTC)."""
    return datetime.combine(day, time(), tzinfo=UTC)


def _item(
    p: GuestyV3Payloads, listing: str, check_in: date, check_out: date, **kw: Any
) -> dict[str, Any]:
    """Build a V3 item for a 16:00 / 11:00 listing in V3's real shape."""
    return p.search_item(
        listing, check_in=_wall(check_in, 16), check_out=_wall(check_out, 11), **kw
    )


def _document(
    check_in: str | None = "16:00", check_out: str | None = "11:00", tz: str = NY
) -> dict[str, Any]:
    """Return a minimal listing document."""
    document: dict[str, Any] = {"_id": "lst", "timezone": tz}
    if check_in is not None:
        document["defaultCheckInTime"] = check_in
    if check_out is not None:
        document["defaultCheckOutTime"] = check_out
    return document


def _map(item: dict[str, Any], document: dict[str, Any]) -> Any:
    """Map *item* for listing ``lst`` with *document*'s stay times."""
    from src.providers.guesty.v3_stay_times import stay_times

    listing = item["stay"][0]["listingId"]
    reservation = map_search_item(
        item, listing, frozenset({listing}), stay_times=stay_times(document)
    )
    assert reservation is not None
    return reservation


def _upcoming(p: GuestyV3Payloads, offset: int = 7) -> date:
    """Return the UTC date *offset* days from today."""
    return (p.today + timedelta(days=offset)).date()


async def _fetch(transport: GuestyFakeTransport, listing: str) -> Any:
    """Fetch *listing*'s only reservation through a V3 provider."""
    provider = transport.provider(reservations_api="v3")
    provider.begin_cycle([listing])
    (reservation,) = await provider.get_reservations(listing)
    return reservation


class TestListingLocalTimes:
    """Check-in/check-out instants are the listing's local times."""

    async def test_eastern_listing_is_not_shifted(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """16:00 / 11:00 New York, not 16:00 / 11:00 UTC (issue #212)."""
        p = guesty_transport.payloads
        listing = p.new_id("lst")
        guesty_transport.add_listing(
            listing, timezone=NY, check_in_time="16:00", check_out_time="11:00"
        )
        check_in, check_out = _upcoming(p, 7), _upcoming(p, 14)
        item = _item(p, listing, check_in, check_out)
        guesty_transport.v3_items = [item]

        reservation = await _fetch(guesty_transport, listing)

        assert reservation.check_in == _local(check_in, 16)
        assert reservation.check_out == _local(check_out, 11)
        (interval,) = reservation.intervals
        assert (interval.check_in, interval.check_out) == (
            _local(check_in, 16),
            _local(check_out, 11),
        )
        assert interval.local_dates == (check_in, check_out)
        assert reservation.pms_booking_id == item["reservationId"]
        assert interval.booking_suffix == listing
        # The listing document already fetched for the scope is reused.
        assert guesty_transport.counts["listing"] == 1

    @pytest.mark.parametrize(
        ("check_in", "check_out", "expected"),
        [
            # EST throughout.
            ("2027-01-10", "2027-01-12", ("21:00", "16:00")),
            # EDT throughout.
            ("2027-07-10", "2027-07-12", ("20:00", "15:00")),
            # Spans the 2027-03-14 spring-forward change.
            ("2027-03-12", "2027-03-16", ("21:00", "15:00")),
            # Spans the 2027-11-07 fall-back change.
            ("2027-11-05", "2027-11-09", ("20:00", "16:00")),
        ],
    )
    def test_daylight_saving_offset_of_each_date(
        self,
        guesty_v3_payloads: GuestyV3Payloads,
        check_in: str,
        check_out: str,
        expected: tuple[str, str],
    ) -> None:
        """Each instant uses the UTC offset in force on its own date."""
        p = guesty_v3_payloads
        start, end = date.fromisoformat(check_in), date.fromisoformat(check_out)
        reservation = _map(_item(p, p.new_id("lst"), start, end), _document())

        assert reservation.check_in == datetime.combine(
            start, time.fromisoformat(expected[0]), tzinfo=UTC
        )
        assert reservation.check_out == datetime.combine(
            end, time.fromisoformat(expected[1]), tzinfo=UTC
        )

    @pytest.mark.parametrize(
        ("check_in_time", "check_out_time", "tz"),
        [
            (None, "11:00", NY),
            ("16:00", None, NY),
            ("4pm", "11:00", NY),
            ("16:00", "25:00", NY),
            ("16:00", "", NY),
            ("16:00", "11:00", "Not/AZone"),
            ("16:00", "11:00", ""),
        ],
    )
    async def test_missing_times_export_an_all_day_stay(
        self,
        guesty_transport: GuestyFakeTransport,
        check_in_time: str | None,
        check_out_time: str | None,
        tz: str,
    ) -> None:
        """No usable time or timezone: dates only, never a guessed time."""
        p = guesty_transport.payloads
        listing = p.new_id("lst")
        guesty_transport.add_listing(
            listing,
            timezone=tz,
            check_in_time=check_in_time,
            check_out_time=check_out_time,
        )
        check_in, check_out = _upcoming(p, 7), _upcoming(p, 10)
        guesty_transport.v3_items = [_item(p, listing, check_in, check_out)]

        reservation = await _fetch(guesty_transport, listing)

        assert (reservation.check_in, reservation.check_out) == (
            _midnight(check_in),
            _midnight(check_out),
        )
        (interval,) = reservation.intervals
        assert (interval.check_in, interval.check_out) == (
            _midnight(check_in),
            _midnight(check_out),
        )
        assert not has_meaningful_times(reservation.check_in, reservation.check_out)

    def test_instant_on_midnight_utc_is_all_day(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """19:00 EST is midnight UTC, which reads as a date: export dates."""
        p = guesty_v3_payloads
        start, end = date(2027, 1, 10), date(2027, 1, 12)
        reservation = _map(_item(p, p.new_id("lst"), start, end), _document("19:00"))

        assert (reservation.check_in, reservation.check_out) == (
            _midnight(start),
            _midnight(end),
        )

    def test_same_day_stay_is_never_inverted(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """16:00 in, 11:00 out on one date would end first: export dates."""
        p = guesty_v3_payloads
        day = date(2027, 1, 10)
        reservation = _map(_item(p, p.new_id("lst"), day, day), _document())

        assert (reservation.check_in, reservation.check_out) == (
            _midnight(day),
            _midnight(day),
        )

    def test_no_listing_times_is_all_day(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """Mapping without any listing document gives dates only."""
        p = guesty_v3_payloads
        listing = p.new_id("lst")
        start, end = date(2027, 1, 10), date(2027, 1, 12)

        reservation = map_search_item(
            _item(p, listing, start, end), listing, frozenset({listing})
        )

        assert reservation is not None
        assert (reservation.check_in, reservation.check_out) == (
            _midnight(start),
            _midnight(end),
        )

    @pytest.mark.parametrize("value", [None, "", "not-a-date"])
    async def test_missing_localized_date_fails_the_listing(
        self, guesty_transport: GuestyFakeTransport, value: str | None
    ) -> None:
        """An unusable localized date keeps the last-good bookings."""
        p = guesty_transport.payloads
        listing = p.new_id("lst")
        guesty_transport.add_listing(listing, timezone=NY)
        item = _item(p, listing, _upcoming(p, 7), _upcoming(p, 10))
        for holder in (item, item["stay"][0]):
            if value is None:
                del holder["checkOutDateLocalized"]
            else:
                holder["checkOutDateLocalized"] = value
        guesty_transport.v3_items = [item]
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([listing])

        with pytest.raises(PMSProviderError, match="checkOutDateLocalized"):
            await provider.get_reservations(listing)


class TestSubUnitTimes:
    """A sub-unit uses its own times when known, else its parent's."""

    async def test_sub_unit_times_fall_back_per_field(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A child entry's own check-in time wins; check-out is inherited."""
        p = guesty_transport.payloads
        parent, unit_a, unit_b = (p.new_id("lst") for _ in range(3))
        document = guesty_transport.add_listing(
            parent,
            timezone=NY,
            children=[unit_a, unit_b],
            check_in_time="16:00",
            check_out_time="11:00",
        )
        document["childListings"][0]["defaultCheckInTime"] = "14:00"
        start = _upcoming(p, 7)
        stay = [
            p.stay_segment(
                unit_a,
                _wall(start, 16),
                _wall(start + timedelta(2), 11),
                parent_listing_id=parent,
            ),
            p.stay_segment(
                unit_b,
                _wall(start + timedelta(3), 16),
                _wall(start + timedelta(5), 11),
                parent_listing_id=parent,
            ),
        ]
        guesty_transport.v3_items = [p.search_item(stay=stay)]

        reservation = await _fetch(guesty_transport, parent)

        assert [
            (i.room_id, i.check_in, i.check_out) for i in reservation.intervals
        ] == [
            (unit_a, _local(start, 14), _local(start + timedelta(2), 11)),
            (
                unit_b,
                _local(start + timedelta(3), 16),
                _local(start + timedelta(5), 11),
            ),
        ]
        assert reservation.check_in == _local(start, 14)
        assert reservation.check_out == _local(start + timedelta(5), 11)

    def test_tracked_room_document_merges_per_field(self) -> None:
        """A tracked room's partial document keeps its entry's other times."""
        from src.providers.guesty.v3_stay_times import ListingTimes, stay_times

        parent = _document("16:00", "11:00")
        parent["childListings"] = [{"_id": "unit", "defaultCheckInTime": "14:00"}]
        own = ListingTimes(timezone=NY, check_in=None, check_out="10:00")

        times = stay_times(parent).with_rooms({"unit": own})

        assert times.for_room("unit") == ListingTimes(NY, "14:00", "10:00")

    async def test_round_trip_keeps_its_identities(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Adjacent segments still merge; ``#n`` ordinals are unchanged."""
        p = guesty_transport.payloads
        parent, unit_a, unit_b = (p.new_id("lst") for _ in range(3))
        guesty_transport.add_listing(
            parent,
            timezone=NY,
            children=[unit_a, unit_b],
            check_in_time="16:00",
            check_out_time="11:00",
        )
        item = p.round_trip_move(parent, unit_a, unit_b)
        guesty_transport.v3_items = [item]

        reservation = await _fetch(guesty_transport, parent)

        assert [i.booking_suffix for i in reservation.intervals] == [
            unit_a,
            unit_b,
            f"{unit_a}#2",
        ]
        for interval, segment in zip(reservation.intervals, item["stay"], strict=True):
            assert interval.check_in == _local(
                date.fromisoformat(segment["checkInDateLocalized"]), 16
            )
            assert interval.check_out == _local(
                date.fromisoformat(segment["checkOutDateLocalized"]), 11
            )


class TestStayTimeDiagnostics:
    """DEBUG evidence of the raw and the derived stay times."""

    async def test_logs_raw_local_and_listing_times(
        self, guesty_transport: GuestyFakeTransport, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Raw ``checkIn``/``checkOut``, local dates and times; no guest PII."""
        p = guesty_transport.payloads
        listing = p.new_id("lst")
        guesty_transport.add_listing(
            listing, timezone=NY, check_in_time="16:00", check_out_time="11:00"
        )
        check_in, check_out = _upcoming(p, 7), _upcoming(p, 14)
        item = _item(p, listing, check_in, check_out)
        guesty_transport.v3_items = [item]
        caplog.set_level(logging.DEBUG, logger="src.providers.guesty")

        await _fetch(guesty_transport, listing)

        (line,) = [
            r.getMessage()
            for r in caplog.records
            if "stay times" in r.getMessage()
            and item["reservationId"] in r.getMessage()
        ]
        for expected in (
            item["checkIn"],
            item["checkOut"],
            check_in.isoformat(),
            check_out.isoformat(),
            "16:00",
            "11:00",
            NY,
        ):
            assert expected in line
        assert "Synthetic Guest" not in caplog.text
