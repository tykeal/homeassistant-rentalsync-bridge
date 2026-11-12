# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Guesty Reservations V3 source (spec 004, US1)."""

from datetime import date
from typing import Any

import pytest
from src.providers.base import InlineCustomFields, PMSOccupancyInterval
from src.providers.guesty.legacy_reservations import _resolve_room_id

from tests.conftest import GuestyFakeTransport


async def _fetch_one(transport: GuestyFakeTransport, listing_id: str) -> Any:
    """Fetch *listing_id* through a V3 provider and return its one result."""
    provider = transport.provider(reservations_api="v3")
    provider.begin_cycle([listing_id])
    (reservation,) = await provider.get_reservations(listing_id)
    return reservation


class TestSingleSegmentMapping:
    """A single-segment search item maps like the legacy record (T017)."""

    async def test_single_unit_reservation(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """IDs, UTC dates, the one interval, guest and custom data."""
        p = guesty_transport.payloads
        listing = p.new_id("lst")
        guesty_transport.add_listing(listing)
        item = p.search_item(
            listing,
            check_in=7,
            check_out=10,
            source="airbnb",
            confirmation_code="SYN-CODE01",
            guests_count=3,
        )
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, listing)

        assert reservation.pms_booking_id == item["reservationId"]
        assert reservation.listing_pms_id == listing
        assert reservation.status == "confirmed"
        assert reservation.check_in == p.check_in_at(7)
        assert reservation.check_out == p.check_out_at(10)
        assert reservation.check_in.utcoffset().total_seconds() == 0
        assert reservation.intervals == (
            PMSOccupancyInterval(
                room_id=listing,
                ordinal=1,
                check_in=p.check_in_at(7),
                check_out=p.check_out_at(10),
                local_dates=(
                    date.fromisoformat(item["checkInDateLocalized"]),
                    date.fromisoformat(item["checkOutDateLocalized"]),
                ),
                guests_count=3,
            ),
        )
        assert reservation.intervals[0].room_id == _resolve_room_id(
            p.as_legacy(item), listing
        )
        assert reservation.room_ids == (listing,)
        assert reservation.guest_id == item["guestId"]
        assert reservation.guest_name is None
        assert reservation.updated_at is None
        assert reservation.custom_data == {
            "source": "airbnb",
            "confirmationCode": "SYN-CODE01",
        }
        # No retiring resource is touched.
        assert guesty_transport.counts["reservations"] == 0

    async def test_multi_unit_sub_unit_room(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A sub-unit stay's room is the sub-unit, as legacy resolves it."""
        p = guesty_transport.payloads
        parent = p.new_id("lst")
        unit = p.new_id("lst")
        guesty_transport.add_listing(parent, children=[unit])
        item = p.search_item(unit, parent_listing_id=parent)
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, parent)

        assert [i.room_id for i in reservation.intervals] == [unit]
        assert reservation.intervals[0].ordinal == 1
        assert reservation.intervals[0].room_id == _resolve_room_id(
            p.as_legacy(item), parent
        )
        assert reservation.room_ids == (unit,)

    @pytest.mark.parametrize(
        ("v3_status", "expected"),
        [
            ("confirmed", "confirmed"),
            ("checked_in", "checked_in"),
            ("checked_out", "checked_out"),
            ("canceled", "cancelled"),
        ],
    )
    async def test_status_mapping(
        self,
        guesty_transport: GuestyFakeTransport,
        v3_status: str,
        expected: str,
    ) -> None:
        """The legacy status map applies unchanged."""
        p = guesty_transport.payloads
        listing = p.new_id("lst")
        guesty_transport.add_listing(listing)
        guesty_transport.v3_items = [p.search_item(listing, status=v3_status)]

        reservation = await _fetch_one(guesty_transport, listing)

        assert reservation.status == expected


class TestInlineCustomFields:
    """``customFields`` parses into the three R8 states (T017)."""

    async def _inline(
        self, transport: GuestyFakeTransport, custom_fields: Any
    ) -> InlineCustomFields:
        """Fetch one item carrying *custom_fields* and return its state."""
        p = transport.payloads
        listing = p.new_id("lst")
        transport.add_listing(listing)
        transport.v3_items = [p.search_item(listing, custom_fields=custom_fields)]
        reservation = await _fetch_one(transport, listing)
        inline: InlineCustomFields = reservation.inline_custom_fields
        return inline

    async def test_present(self, guesty_transport: GuestyFakeTransport) -> None:
        """A well-formed list maps to ``{fieldId: value}``."""
        fields = guesty_transport.payloads.custom_fields(
            {"6620door": "4321", "6620wifi": "synthetic-wifi"}
        )

        inline = await self._inline(guesty_transport, fields)

        assert inline.state == "present"
        assert dict(inline.values) == {
            "6620door": "4321",
            "6620wifi": "synthetic-wifi",
        }

    async def test_absent(self, guesty_transport: GuestyFakeTransport) -> None:
        """No ``customFields`` key means no value is set."""
        inline = await self._inline(guesty_transport, None)

        assert inline.state == "absent"
        assert dict(inline.values) == {}

    @pytest.mark.parametrize(
        "malformed",
        [
            "not-a-list",
            {"fieldId": "6620door", "value": "4321"},
            [{"value": "4321"}],
            [{"fieldId": 5, "value": "4321"}],
            ["6620door"],
        ],
    )
    async def test_malformed(
        self, guesty_transport: GuestyFakeTransport, malformed: Any
    ) -> None:
        """A non-list, or any malformed element, is MALFORMED with no values."""
        inline = await self._inline(guesty_transport, malformed)

        assert inline.state == "malformed"
        assert dict(inline.values) == {}
