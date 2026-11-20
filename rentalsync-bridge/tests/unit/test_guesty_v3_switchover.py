# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""A mid-stay room move keeps door access continuous (#212, #215).

The interval of the room a guest moves into starts at the previous room's
check-out time on the switch date, not at its own check-in time, so there
is no gap between the two rooms' access windows.
"""

from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from src.providers.base import PMSReservation
from src.providers.guesty.v3_mapping import map_search_item
from src.providers.guesty.v3_stay_times import ListingTimes, StayTimes

from tests.conftest import GuestyFakeTransport

NY = "America/New_York"
CHI = "America/Chicago"
START = date(2026, 12, 7)
RID = "res0000000000000000000001"


def _local(day: date, hour: int, tz: str = NY) -> datetime:
    """Return the UTC instant of *day* at *hour* in *tz*."""
    return datetime.combine(day, time(hour), tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _segment(
    listing: str, offset: int, nights: int, parent: str | None = None
) -> dict[str, Any]:
    """Build a V3 ``stay[]`` segment, local times labelled ``Z``."""
    check_in, check_out = START + timedelta(offset), START + timedelta(offset + nights)
    segment: dict[str, Any] = {
        "listingId": listing,
        "checkIn": f"{check_in.isoformat()}T16:00:00.000Z",
        "checkOut": f"{check_out.isoformat()}T11:00:00.000Z",
        "checkInDateLocalized": check_in.isoformat(),
        "checkOutDateLocalized": check_out.isoformat(),
        "guestsCount": 2,
    }
    if parent is not None:
        segment["parentListingId"] = parent
    return segment


def _contiguous(*rooms: tuple[str, str | None]) -> list[dict[str, Any]]:
    """Build back-to-back two-night segments in *rooms*."""
    segments = [
        _segment(room, 2 * i, 2, parent) for i, (room, parent) in enumerate(rooms)
    ]
    for previous, segment in pairwise(segments):
        # Guesty reports one shared boundary instant at a switch-over.
        segment["checkIn"] = previous["checkOut"]
    return segments


def _item(stay: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap *stay* in a confirmed V3 search item."""
    return {
        "reservationId": RID,
        "status": "confirmed",
        "guestId": "guest",
        "checkIn": stay[0]["checkIn"],
        "checkOut": stay[-1]["checkOut"],
        "checkInDateLocalized": stay[0]["checkInDateLocalized"],
        "checkOutDateLocalized": stay[-1]["checkOutDateLocalized"],
        "stay": stay,
    }


def _map(
    stay: list[dict[str, Any]], listing: str, scope: set[str], times: StayTimes
) -> PMSReservation:
    """Map *stay* for *listing* owning *scope*."""
    reservation = map_search_item(
        _item(stay), listing, frozenset(scope), stay_times=times
    )
    assert reservation is not None
    return reservation


def _spans(reservation: PMSReservation) -> list[tuple[str, Any, Any]]:
    """Return each interval's booking suffix and instants."""
    return [(i.booking_suffix, i.check_in, i.check_out) for i in reservation.intervals]


PARENT_TIMES = StayTimes(
    ListingTimes(NY, "16:00", "11:00"),
    {"a": ListingTimes(NY, "15:00", "10:00"), "b": ListingTimes(NY, "17:00", "12:00")},
)


class TestSwitchOver:
    """The next room starts at the previous room's check-out instant."""

    def test_move_between_rooms_of_one_listing(self) -> None:
        """A → B in one property: B opens when A closes."""
        stay = _contiguous(("a", "p"), ("b", "p"))

        reservation = _map(stay, "p", {"p", "a", "b"}, PARENT_TIMES)

        switch = START + timedelta(2)
        assert _spans(reservation) == [
            ("a", _local(START, 15), _local(switch, 10)),
            ("b", _local(switch, 10), _local(switch + timedelta(2), 12)),
        ]
        assert reservation.check_in == _local(START, 15)
        assert reservation.check_out == _local(switch + timedelta(2), 12)

    def test_merged_room_keeps_its_switch_over_start(self) -> None:
        """A → B → B: the merged B interval still opens at A's check-out."""
        stay = _contiguous(("a", "p"), ("b", "p"), ("b", "p"))

        reservation = _map(stay, "p", {"p", "a", "b"}, PARENT_TIMES)

        switch = START + timedelta(2)
        assert _spans(reservation)[1] == (
            "b",
            _local(switch, 10),
            _local(switch + timedelta(4), 12),
        )

    def test_move_between_tracked_listings(self) -> None:
        """A → B across tracked listings: B opens at A's own check-out."""
        stay = _contiguous(("a", None), ("b", None))
        times = StayTimes(
            ListingTimes(NY, "16:00", "11:00"),
            {"a": ListingTimes(CHI, "15:00", "10:00")},
        )

        reservation = _map(stay, "b", {"b"}, times)

        switch = START + timedelta(2)
        opens = _local(switch, 10, CHI)
        assert _spans(reservation) == [
            ("b", opens, _local(switch + timedelta(2), 11)),
        ]
        assert reservation.check_in == opens

    def test_unknown_previous_listing_uses_own_check_in(self) -> None:
        """A → B from an untracked listing: B opens at its own check-in."""
        stay = _contiguous(("a", None), ("b", None))
        times = StayTimes(ListingTimes(NY, "16:00", "11:00"))

        reservation = _map(stay, "b", {"b"}, times)

        switch = START + timedelta(2)
        assert _spans(reservation) == [
            ("b", _local(switch, 16), _local(switch + timedelta(2), 11)),
        ]

    def test_previous_listing_without_check_out_uses_own_check_in(self) -> None:
        """A known previous listing with no check-out time is not guessed."""
        stay = _contiguous(("a", None), ("b", None))
        times = StayTimes(
            ListingTimes(NY, "16:00", "11:00"), {"a": ListingTimes(CHI, "15:00", None)}
        )

        reservation = _map(stay, "b", {"b"}, times)

        switch = START + timedelta(2)
        assert _spans(reservation)[0][1] == _local(switch, 16)

    async def test_move_between_tracked_multi_unit_listings(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """P's unit → Q's unit: Q's unit opens at P's unit's check-out."""
        p = guesty_transport.payloads
        parent_p, unit_p, parent_q, unit_q = (p.new_id("lst") for _ in range(4))
        document = guesty_transport.add_listing(
            parent_p, children=[unit_p], timezone=CHI, check_out_time="11:00"
        )
        document["childListings"][0]["defaultCheckOutTime"] = "10:00"
        guesty_transport.add_listing(parent_q, children=[unit_q], timezone=NY)
        stay = _contiguous((unit_p, parent_p), (unit_q, parent_q))
        guesty_transport.v3_items = [_item(stay)]
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([parent_p, parent_q])

        (reservation,) = await provider.get_reservations(parent_q)

        assert _spans(reservation)[0][1] == _local(START + timedelta(2), 10, CHI)


class TestUnchanged:
    """Stays without a switch-over keep their #215 instants and identity."""

    def test_single_segment(self) -> None:
        """A one-room stay uses its listing's own times."""
        reservation = _map(_contiguous(("a", "p")), "p", {"p", "a"}, PARENT_TIMES)

        assert reservation.pms_booking_id == RID
        assert _spans(reservation) == [
            ("a", _local(START, 15), _local(START + timedelta(2), 10)),
        ]

    def test_same_room_segments_merge(self) -> None:
        """Adjacent same-room segments are still one interval."""
        stay = _contiguous(("a", "p"), ("a", "p"))

        reservation = _map(stay, "p", {"p", "a"}, PARENT_TIMES)

        assert _spans(reservation) == [
            ("a", _local(START, 15), _local(START + timedelta(4), 10)),
        ]

    def test_gap_between_rooms_is_not_a_switch_over(self) -> None:
        """A later move after a gap opens at the next room's check-in."""
        stay = [_segment("a", 0, 2, "p"), _segment("b", 3, 2, "p")]

        reservation = _map(stay, "p", {"p", "a", "b"}, PARENT_TIMES)

        assert _spans(reservation)[1][1] == _local(START + timedelta(3), 17)

    def test_round_trip_keeps_its_identities(self) -> None:
        """A → B → A keeps its suffixes; only the starts move."""
        stay = _contiguous(("a", "p"), ("b", "p"), ("a", "p"))

        reservation = _map(stay, "p", {"p", "a", "b"}, PARENT_TIMES)

        assert reservation.pms_booking_id == RID
        assert [s for s, _, _ in _spans(reservation)] == ["a", "b", "a#2"]
        assert [start for _, start, _ in _spans(reservation)] == [
            _local(START, 15),
            _local(START + timedelta(2), 10),
            _local(START + timedelta(4), 12),
        ]
