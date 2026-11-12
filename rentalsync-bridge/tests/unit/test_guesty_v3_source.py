# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Guesty Reservations V3 source (spec 004, US1)."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from src.providers.base import (
    InlineCustomFields,
    PMSOccupancyInterval,
    PMSProviderError,
)
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


def _filter_ids(transport: GuestyFakeTransport) -> list[set[str]]:
    """Return each recorded search's ``filter[listingId][in]`` ID set."""
    return [
        set(request.query["filter[listingId][in]"][0].split(","))
        for request in transport.requests_for("v3_search")
    ]


class TestCombinedSearch:
    """One combined search serves every tracked listing (T018, R2)."""

    @pytest.fixture
    def account(self, guesty_transport: GuestyFakeTransport) -> dict[str, Any]:
        """Two single-unit listings and a multi-unit parent, one stay each."""
        p = guesty_transport.payloads
        a, b, parent = p.new_id("lst"), p.new_id("lst"), p.new_id("lst")
        units = [p.new_id("lst"), p.new_id("lst")]
        guesty_transport.add_listing(a)
        guesty_transport.add_listing(b)
        guesty_transport.add_listing(parent, children=units)
        items = {
            a: p.search_item(a),
            b: p.search_item(b),
            parent: p.search_item(units[0], parent_listing_id=parent),
        }
        guesty_transport.v3_items = list(items.values())
        return {"a": a, "b": b, "parent": parent, "units": units, "items": items}

    async def test_one_search_per_cycle(
        self, guesty_transport: GuestyFakeTransport, account: dict[str, Any]
    ) -> None:
        """Three listings share one page sequence; each gets only its own."""
        provider = guesty_transport.provider(reservations_api="v3")
        tracked = [account["a"], account["b"], account["parent"]]
        provider.begin_cycle(tracked)

        for listing in tracked:
            (reservation,) = await provider.get_reservations(listing)
            expected = account["items"][listing]["reservationId"]
            assert reservation.pms_booking_id == expected

        assert guesty_transport.counts["v3_search"] == 1
        assert guesty_transport.counts["listing"] == 3
        assert _filter_ids(guesty_transport) == [
            {account["a"], account["b"], account["parent"], *account["units"]}
        ]

    async def test_new_cycle_searches_again(
        self, guesty_transport: GuestyFakeTransport, account: dict[str, Any]
    ) -> None:
        """``begin_cycle`` drops the previous cycle's result and scopes."""
        provider = guesty_transport.provider(reservations_api="v3")
        tracked = [account["a"], account["b"]]
        for _cycle in range(2):
            provider.begin_cycle(tracked)
            for listing in tracked:
                await provider.get_reservations(listing)

        assert guesty_transport.counts["v3_search"] == 2
        assert guesty_transport.counts["listing"] == 4

    async def test_undeclared_listing_joins_and_searches_afresh(
        self, guesty_transport: GuestyFakeTransport, account: dict[str, Any]
    ) -> None:
        """A listing outside the declared set is added to a fresh search."""
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([account["a"], account["b"]])

        await provider.get_reservations(account["a"])
        (reservation,) = await provider.get_reservations(account["parent"])
        await provider.get_reservations(account["b"])

        expected = account["items"][account["parent"]]["reservationId"]
        assert reservation.pms_booking_id == expected
        assert guesty_transport.counts["v3_search"] == 2
        assert guesty_transport.counts["listing"] == 3
        first, second = _filter_ids(guesty_transport)
        assert first == {account["a"], account["b"]}
        assert second == {
            account["a"],
            account["b"],
            account["parent"],
            *account["units"],
        }

    async def test_explicit_bounds_widen_the_search(
        self, guesty_transport: GuestyFakeTransport, account: dict[str, Any]
    ) -> None:
        """Bounds beyond the searched window re-search; they are never trimmed."""
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([account["a"], account["b"]])

        await provider.get_reservations(account["a"])
        await provider.get_reservations(
            account["b"],
            start_date=datetime(2020, 1, 1, tzinfo=UTC),
            end_date=datetime(2040, 1, 1, tzinfo=UTC),
        )
        await provider.get_reservations(account["a"])

        assert guesty_transport.counts["v3_search"] == 2
        widened = guesty_transport.requests_for("v3_search")[-1].query
        assert widened["filter[checkOut][gte]"] == ["2019-12-31"]
        assert widened["filter[checkIn][lte]"] == ["2040-01-02"]

    async def test_filter_is_union_of_fetch_scopes(
        self, guesty_transport: GuestyFakeTransport, account: dict[str, Any]
    ) -> None:
        """Sub-units of a tracked parent and an imported child's parent."""
        p = guesty_transport.payloads
        other_parent, child = p.new_id("lst"), p.new_id("lst")
        sibling = p.new_id("lst")
        guesty_transport.add_listing(other_parent, children=[child, sibling])
        guesty_transport.add_listing(child, parent_id=other_parent)
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([account["parent"], child])

        await provider.get_reservations(child)

        assert _filter_ids(guesty_transport) == [
            {account["parent"], *account["units"], child, other_parent}
        ]

    async def test_scope_failure_aborts_only_that_listing(
        self, guesty_transport: GuestyFakeTransport, account: dict[str, Any]
    ) -> None:
        """A failed listing document fails that listing; its ID is still sent."""
        # Scopes resolve in sorted order, so the first listing document
        # request is account["a"]'s.
        guesty_transport.fail("listing", 500)
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([account["a"], account["b"]])

        (reservation,) = await provider.get_reservations(account["b"])
        with pytest.raises(PMSProviderError):
            await provider.get_reservations(account["a"])

        expected = account["items"][account["b"]]["reservationId"]
        assert reservation.pms_booking_id == expected
        assert guesty_transport.counts["v3_search"] == 1
        assert guesty_transport.counts["listing"] == 2
        assert _filter_ids(guesty_transport) == [{account["a"], account["b"]}]


class TestChunking:
    """Filter IDs are split into family-preserving chunks (T019, FR-016a)."""

    @pytest.mark.parametrize(("listings", "chunk_sizes"), [(50, [50]), (51, [50, 1])])
    async def test_fifty_ids_per_chunk(
        self,
        guesty_transport: GuestyFakeTransport,
        listings: int,
        chunk_sizes: list[int],
    ) -> None:
        """50 filter IDs make one search; 51 make two."""
        p = guesty_transport.payloads
        ids = [p.new_id("lst") for _ in range(listings)]
        for listing in ids:
            guesty_transport.add_listing(listing)
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle(ids)

        await provider.get_reservations(ids[0])

        chunks = _filter_ids(guesty_transport)
        assert [len(chunk) for chunk in chunks] == chunk_sizes
        assert set().union(*chunks) == set(ids)
        for request in guesty_transport.requests_for("v3_search"):
            value = request.query["filter[listingId][in]"][0].split(",")
            assert value == sorted(value)

    async def test_family_is_not_split(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A multi-unit family that would straddle 50 moves whole."""
        p = guesty_transport.payloads
        singles = [p.new_id("lst") for _ in range(48)]
        parent = p.new_id("lst")
        units = [p.new_id("lst") for _ in range(3)]
        for listing in singles:
            guesty_transport.add_listing(listing)
        guesty_transport.add_listing(parent, children=units)
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([*singles, parent])

        await provider.get_reservations(parent)

        chunks = _filter_ids(guesty_transport)
        assert [len(chunk) for chunk in chunks] == [48, 4]
        assert chunks[1] == {parent, *units}

    @pytest.fixture
    def oversized(self, guesty_transport: GuestyFakeTransport) -> dict[str, Any]:
        """A parent with 51 units (52 IDs) and a stay in the last unit.

        The parent sorts first and the unit last, so the stay's first
        segment matches both chunks of the split family.
        """
        p = guesty_transport.payloads
        parent = p.new_id("lst")
        units = [p.new_id("lst") for _ in range(51)]
        guesty_transport.add_listing(parent, children=units)
        item = p.search_item(units[-1], parent_listing_id=parent)
        guesty_transport.v3_items = [item]
        return {"parent": parent, "item": item}

    async def test_identical_copies_are_kept_once(
        self, guesty_transport: GuestyFakeTransport, oversized: dict[str, Any]
    ) -> None:
        """Only an oversized family is split; a duplicate is merged."""
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([oversized["parent"]])

        reservations = await provider.get_reservations(oversized["parent"])

        assert [len(chunk) for chunk in _filter_ids(guesty_transport)] == [50, 2]
        assert [r.pms_booking_id for r in reservations] == [
            oversized["item"]["reservationId"]
        ]

    async def test_conflicting_copies_fail_the_search(
        self,
        guesty_transport: GuestyFakeTransport,
        oversized: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A copy that changed between chunk requests aborts (FR-011)."""
        serve = guesty_transport._serve_v3_search

        def serve_then_change(*args: Any) -> Any:
            """Serve a page, then change the stay's source for the next."""
            response = serve(*args)
            oversized["item"]["source"] = "changed-between-chunks"
            return response

        monkeypatch.setattr(guesty_transport, "_serve_v3_search", serve_then_change)
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([oversized["parent"]])

        with pytest.raises(PMSProviderError):
            await provider.get_reservations(oversized["parent"])
        assert guesty_transport.counts["v3_search"] == 2
