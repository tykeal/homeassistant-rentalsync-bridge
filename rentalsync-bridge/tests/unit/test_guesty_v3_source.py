# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Guesty Reservations V3 source (spec 004, US1-US5)."""

import copy
import itertools
import logging
from datetime import UTC, date, datetime
from typing import Any

import pytest
from src.providers.base import (
    HOLD_STATUSES,
    InlineCustomFields,
    PMSOccupancyInterval,
    PMSProviderError,
)
from src.providers.guesty import reservations_v3, v3_search
from src.providers.guesty.legacy_reservations import (
    LegacyReservationSource,
    _resolve_room_id,
)
from src.providers.guesty.v3_by_ids import (
    ambiguous_rooms,
    map_by_ids_item,
    normalise_by_ids_item,
)
from src.providers.guesty.v3_mapping import change_key, map_search_item
from src.services.sync_service import SyncService

from tests.conftest import GuestyFakeTransport, GuestyV3Payloads


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
            "nightsCount": 3,
            "guestsCount": 3,
            "notes": "Synthetic other note; Synthetic guest note",
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
            [{"fieldId": "6620door"}],
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


def _description(item: dict[str, Any], *scope: str) -> dict[str, Any]:
    """Map *item* for a listing owning *scope* and return its custom data."""
    owned = frozenset(scope or {item["stay"][0]["listingId"]})
    reservation = map_search_item(item, next(iter(owned)), owned)
    assert reservation is not None
    return dict(reservation.custom_data)


class TestDescriptionFields:
    """Description-field keys keep their meaning under V3 (T055, research R10)."""

    def test_single_segment_matches_legacy(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """Every key V3 still supplies renders exactly as legacy does."""
        p = guesty_v3_payloads
        listing = p.new_id("lst")
        item = p.search_item(listing, check_in=7, check_out=11, guests_count=4)
        legacy = p.as_legacy(item)

        v3 = map_search_item(item, listing, frozenset({listing}))
        assert v3 is not None
        legacy_mapped = LegacyReservationSource._map_reservation(legacy, listing)
        assert legacy_mapped is not None
        legacy_rendered = SyncService._base_custom_data(legacy_mapped)

        assert SyncService._base_custom_data(v3) == {
            key: value
            for key, value in legacy_rendered.items()
            if not key.startswith("money_")
        }
        assert {"guestsCount", "nightsCount", "notes"} <= set(legacy_rendered)

    def test_guests_count_is_the_segment_count(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """A single segment's ``guestsCount`` is the reservation's count."""
        p = guesty_v3_payloads
        item = p.search_item(p.new_id("lst"), guests_count=3)

        assert _description(item)["guestsCount"] == 3

    @pytest.mark.parametrize(("raw", "rendered"), [(3, "3"), (3.0, "3")])
    def test_guests_count_renders_as_an_int_string(
        self, guesty_v3_payloads: GuestyV3Payloads, raw: Any, rendered: str
    ) -> None:
        """An integral count renders ``3``, never ``3.0``."""
        p = guesty_v3_payloads
        listing = p.new_id("lst")
        item = p.search_item(listing)
        item["stay"][0]["guestsCount"] = raw

        reservation = map_search_item(item, listing, frozenset({listing}))
        assert reservation is not None

        assert reservation.intervals[0].guests_count == 3
        assert SyncService._base_custom_data(reservation)["guestsCount"] == rendered

    @pytest.mark.parametrize("raw", [None, "3", 2.5, True])
    def test_unusable_guests_count_is_omitted(
        self, guesty_v3_payloads: GuestyV3Payloads, raw: Any
    ) -> None:
        """A missing or non-integral count omits the key, as legacy does."""
        p = guesty_v3_payloads
        item = p.search_item(p.new_id("lst"), guests_count=None)
        if raw is not None:
            item["stay"][0]["guestsCount"] = raw

        assert "guestsCount" not in _description(item)

    def test_guests_count_is_the_maximum_of_merged_segments(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """Adjacent same-room segments merge non-additively (max)."""
        p = guesty_v3_payloads
        listing = p.new_id("lst")
        stay = p._segments([(listing, None), (listing, None)], 5, [2, 2])
        stay[0]["guestsCount"], stay[1]["guestsCount"] = 2, 4
        item = p.search_item(stay=stay)

        reservation = map_search_item(item, listing, frozenset({listing}))
        assert reservation is not None

        assert [i.guests_count for i in reservation.intervals] == [4]
        assert reservation.custom_data["guestsCount"] == 4

    def test_guests_count_is_per_interval(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """Each interval carries its own count; the reservation the maximum."""
        p = guesty_v3_payloads
        parent, unit_a, unit_b = (p.new_id("lst") for _ in range(3))
        item = p.same_property_move(parent, unit_a, unit_b)
        item["stay"][0]["guestsCount"], item["stay"][1]["guestsCount"] = 2, 5
        scope = frozenset({parent, unit_a, unit_b})

        reservation = map_search_item(item, parent, scope)
        assert reservation is not None

        assert [i.guests_count for i in reservation.intervals] == [2, 5]
        assert reservation.custom_data["guestsCount"] == 5

    def test_notes_join_in_order_and_skip_secrets(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """other, guest, specialRequests joined; cleaning and keyCode never."""
        p = guesty_v3_payloads
        item = p.search_item(
            p.new_id("lst"),
            notes={
                "keyCode": "0000",
                "specialRequests": "  Synthetic request  ",
                "cleaning": "Synthetic cleaning note",
                "guest": "",
                "other": "Synthetic other note",
            },
        )

        notes = _description(item)["notes"]

        assert notes == "Synthetic other note; Synthetic request"
        assert "0000" not in notes
        assert "cleaning" not in notes

    @pytest.mark.parametrize(
        "notes",
        [
            {"other": "", "guest": "  ", "specialRequests": ""},
            {"cleaning": "Synthetic cleaning note", "keyCode": "0000"},
            {"other": 5, "guest": None},
            {},
        ],
        ids=["blank", "excluded-only", "non-string", "empty"],
    )
    def test_notes_without_text_are_omitted(
        self, guesty_v3_payloads: GuestyV3Payloads, notes: dict[str, Any]
    ) -> None:
        """No usable note text omits the key."""
        p = guesty_v3_payloads
        item = p.search_item(p.new_id("lst"), notes=notes)

        assert "notes" not in _description(item)

    @pytest.mark.parametrize("notes", [None, "Synthetic text", ["Synthetic text"]])
    def test_notes_of_an_unexpected_shape_are_omitted(
        self, guesty_v3_payloads: GuestyV3Payloads, notes: Any
    ) -> None:
        """A missing or non-object ``notes`` omits the key without failing."""
        p = guesty_v3_payloads
        item = p.search_item(p.new_id("lst"))
        if notes is None:
            del item["notes"]
        else:
            item["notes"] = notes

        assert "notes" not in _description(item)

    def test_nights_count_spans_the_whole_reservation(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """The reservation's nights, even for a listing owning one segment."""
        p = guesty_v3_payloads
        listing_a, listing_b = p.new_id("lst"), p.new_id("lst")
        item = p.tracked_listing_move(listing_a, listing_b)

        assert _description(item, listing_b)["nightsCount"] == 4
        assert p.as_legacy(item)["nightsCount"] == 4

    def test_nights_count_uses_the_localized_dates(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """Nights follow the listing's local calendar, not UTC instants."""
        p = guesty_v3_payloads
        item = p.search_item(p.new_id("lst"), check_in=7, check_out=10)
        item["checkInDateLocalized"] = "2030-03-01"
        item["checkOutDateLocalized"] = "2030-03-06"

        assert _description(item)["nightsCount"] == 5

    @pytest.mark.parametrize(
        ("check_in", "check_out"),
        [
            ("not-a-date", "2030-03-06"),
            ("2030-03-01", None),
            (None, None),
            ("2030-03-06", "2030-03-06"),
            ("2030-03-06", "2030-03-01"),
        ],
        ids=["garbage", "missing-out", "missing-both", "zero", "negative"],
    )
    def test_unusable_dates_omit_nights_count(
        self,
        guesty_v3_payloads: GuestyV3Payloads,
        check_in: str | None,
        check_out: str | None,
    ) -> None:
        """Unparsable or non-positive localized dates omit the key."""
        p = guesty_v3_payloads
        item = p.search_item(p.new_id("lst"))
        for key, value in (
            ("checkInDateLocalized", check_in),
            ("checkOutDateLocalized", check_out),
        ):
            if value is None:
                item.pop(key)
            else:
                item[key] = value
            for segment in item["stay"]:
                segment.pop(key, None)

        assert "nightsCount" not in _description(item)

    def test_no_money_fields(self, guesty_v3_payloads: GuestyV3Payloads) -> None:
        """Payment totals are retired, never re-sourced (FR-036)."""
        p = guesty_v3_payloads
        item = p.search_item(p.new_id("lst"))
        item["money"] = {"totalPaid": 450, "balanceDue": 0, "currency": "USD"}

        assert not [key for key in _description(item) if key.startswith("money")]


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


# Research R4 page faults, each injected on the second page of a chunk.
R4_PAGE_FAULTS = (
    "malformed_results",
    "missing_pagination",
    "has_more_not_bool",
    "skip_mismatch",
    "over_limit",
    "empty_has_more",
    "short_has_more",
    "missing_reservation_id",
    "duplicate",
    "duplicate_across_pages",
    "out_of_order",
)


class TestCompletenessGuard:
    """Every R4 guard check aborts the whole cycle (T029, FR-008-FR-011)."""

    @pytest.fixture
    def paged(
        self, guesty_transport: GuestyFakeTransport, monkeypatch: pytest.MonkeyPatch
    ) -> dict[str, Any]:
        """Two listings in separate chunks, five stays each, two per page."""
        monkeypatch.setattr(v3_search, "DEFAULT_PAGE_LIMIT", 2)
        monkeypatch.setattr(
            reservations_v3,
            "pack_chunks",
            lambda families: v3_search.pack_chunks(families, limit=1),
        )
        p = guesty_transport.payloads
        a, b = p.new_id("lst"), p.new_id("lst")
        guesty_transport.add_listing(a)
        guesty_transport.add_listing(b)
        guesty_transport.v3_items = [
            p.search_item(listing, check_in=n, check_out=n + 2)
            for listing in (a, b)
            for n in range(5)
        ]
        return {"a": a, "b": b}

    @staticmethod
    async def _assert_cycle_aborts(
        transport: GuestyFakeTransport, paged: dict[str, Any]
    ) -> None:
        """Both listings raise; the second without a single new request."""
        provider = transport.provider(reservations_api="v3")
        provider.begin_cycle([paged["a"], paged["b"]])

        with pytest.raises(PMSProviderError):
            await provider.get_reservations(paged["a"])
        requests = len(transport.requests)
        with pytest.raises(PMSProviderError):
            await provider.get_reservations(paged["b"])
        assert len(transport.requests) == requests

    @pytest.mark.parametrize("chunk", ["a", "b"], ids=["first", "second"])
    @pytest.mark.parametrize("kind", R4_PAGE_FAULTS)
    async def test_page_fault_aborts_the_cycle(
        self,
        guesty_transport: GuestyFakeTransport,
        paged: dict[str, Any],
        kind: str,
        chunk: str,
    ) -> None:
        """A faulty page in either chunk raises; nothing partial is returned."""
        guesty_transport.fault_search_page(1, kind, listing_id=paged[chunk])

        await self._assert_cycle_aborts(guesty_transport, paged)

    async def test_page_cap_aborts(
        self,
        guesty_transport: GuestyFakeTransport,
        paged: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Still ``hasMore`` after the last allowed page (check 8)."""
        monkeypatch.setattr(v3_search, "MAX_RESERVATION_PAGES", 2)

        await self._assert_cycle_aborts(guesty_transport, paged)
        assert guesty_transport.counts["v3_search"] == 2

    async def test_rate_limit_after_retries_aborts(
        self, guesty_transport: GuestyFakeTransport, paged: dict[str, Any]
    ) -> None:
        """A page still rate-limited after the retries (check 9, FR-033)."""
        guesty_transport.fail("v3_search", 429, times=4)

        await self._assert_cycle_aborts(guesty_transport, paged)
        assert guesty_transport.counts["v3_search"] == 4


class TestRejectedSearch:
    """A rejected search or a status-less item is a sync error (T030)."""

    async def test_http_400_names_status_and_excerpt(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """FR-021: never an empty result, and the cause is named."""
        p = guesty_transport.payloads
        listing = p.new_id("lst")
        guesty_transport.add_listing(listing)
        guesty_transport.v3_items = [p.search_item(listing)]
        guesty_transport.fail(
            "v3_search", 400, body={"message": "Unsupported filter operator"}
        )
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([listing])

        with pytest.raises(PMSProviderError) as excinfo:
            await provider.get_reservations(listing)

        message = str(excinfo.value)
        assert message.startswith("Guesty rejected the reservation search: 400")
        assert "Unsupported filter operator" in message

    async def test_missing_status_aborts_every_listing(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """FR-026: a status-less item anywhere in the search aborts it."""
        p = guesty_transport.payloads
        a, b = p.new_id("lst"), p.new_id("lst")
        guesty_transport.add_listing(a)
        guesty_transport.add_listing(b)
        guesty_transport.v3_items = [p.search_item(a), p.search_item(b, status=None)]
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([a, b])

        with pytest.raises(PMSProviderError, match="status"):
            await provider.get_reservations(a)
        with pytest.raises(PMSProviderError, match="status"):
            await provider.get_reservations(b)
        assert guesty_transport.counts["v3_search"] == 1


def _ids(rid: str, reservation: Any) -> list[str]:
    """Return a reservation's interval booking IDs."""
    return [f"{rid}::{i.booking_suffix}" for i in reservation.intervals]


def _spans(reservation: Any) -> list[tuple[datetime | None, datetime | None]]:
    """Return a reservation's interval ``(check_in, check_out)`` instants."""
    return [(i.check_in, i.check_out) for i in reservation.intervals]


class TestAttribution:
    """Segment-level ownership and interval identities (T039, R6)."""

    @pytest.fixture
    def property_(self, guesty_transport: GuestyFakeTransport) -> dict[str, str]:
        """A tracked multi-unit parent with sub-units A and B."""
        p = guesty_transport.payloads
        parent, unit_a, unit_b = p.new_id("lst"), p.new_id("lst"), p.new_id("lst")
        guesty_transport.add_listing(parent, children=[unit_a, unit_b])
        return {"parent": parent, "a": unit_a, "b": unit_b}

    async def test_assigned_single_unit(
        self, guesty_transport: GuestyFakeTransport, property_: dict[str, str]
    ) -> None:
        """A stay assigned to one sub-unit is ``{rid}::{unit}`` (US3-1)."""
        p = guesty_transport.payloads
        item = p.search_item(property_["b"], parent_listing_id=property_["parent"])
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, property_["parent"])

        rid = item["reservationId"]
        assert _ids(rid, reservation) == [f"{rid}::{property_['b']}"]
        assert reservation.room_ids == (property_["b"],)

    async def test_unassigned_parent_is_listing_only(
        self, guesty_transport: GuestyFakeTransport, property_: dict[str, str]
    ) -> None:
        """An unassigned stay stays on the parent, as today (US3-2, FR-019)."""
        p = guesty_transport.payloads
        item = p.unassigned_parent(property_["parent"])
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, property_["parent"])

        rid = item["reservationId"]
        assert _ids(rid, reservation) == [f"{rid}::{property_['parent']}"]

    async def test_new_sub_unit_is_owned_through_its_parent(
        self, guesty_transport: GuestyFakeTransport, property_: dict[str, str]
    ) -> None:
        """A sub-unit the listing document does not list yet still belongs."""
        p = guesty_transport.payloads
        unknown = p.new_id("lst")
        item = p.search_item(unknown, parent_listing_id=property_["parent"])
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, property_["parent"])

        assert reservation.room_ids == (unknown,)

    async def test_move_from_another_tracked_listing(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Each tracked listing claims only its own segment's dates (US3-3)."""
        p = guesty_transport.payloads
        first, second = p.new_id("lst"), p.new_id("lst")
        guesty_transport.add_listing(first)
        guesty_transport.add_listing(second)
        item = p.tracked_listing_move(first, second)
        guesty_transport.v3_items = [item]
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([first, second])

        (later,) = await provider.get_reservations(second)
        (earlier,) = await provider.get_reservations(first)

        rid = item["reservationId"]
        segment_x, segment_y = item["stay"]
        assert _ids(rid, later) == [f"{rid}::{second}"]
        assert (later.check_in, later.check_out) == (
            datetime.fromisoformat(segment_y["checkIn"]),
            datetime.fromisoformat(segment_y["checkOut"]),
        )
        assert _spans(later) == [(later.check_in, later.check_out)]
        assert later.intervals[0].local_dates == (
            date.fromisoformat(segment_y["checkInDateLocalized"]),
            date.fromisoformat(segment_y["checkOutDateLocalized"]),
        )
        assert _ids(rid, earlier) == [f"{rid}::{first}"]
        assert (earlier.check_in, earlier.check_out) == (
            datetime.fromisoformat(segment_x["checkIn"]),
            datetime.fromisoformat(segment_x["checkOut"]),
        )
        assert guesty_transport.counts["v3_search"] == 1

    async def test_untracked_start_is_the_residual_gap(
        self,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Not found while its first segment is untracked; claimed once it is."""
        p = guesty_transport.payloads
        untracked, tracked = p.new_id("lst"), p.new_id("lst")
        guesty_transport.add_listing(untracked)
        guesty_transport.add_listing(tracked)
        item = p.never_stored_untracked_start(untracked, tracked)
        rid = item["reservationId"]
        guesty_transport.v3_items = [item]
        provider = guesty_transport.provider(reservations_api="v3")

        with caplog.at_level(logging.DEBUG):
            provider.begin_cycle([tracked])
            assert await provider.get_reservations(tracked) == []
        assert _filter_ids(guesty_transport) == [{tracked}]
        assert not [r for r in caplog.records if rid in r.getMessage()]

        provider.begin_cycle([tracked, untracked])
        (reservation,) = await provider.get_reservations(tracked)

        assert _ids(rid, reservation) == [f"{rid}::{tracked}"]
        assert reservation.check_in == datetime.fromisoformat(
            item["stay"][1]["checkIn"]
        )

    async def test_same_property_move(
        self, guesty_transport: GuestyFakeTransport, property_: dict[str, str]
    ) -> None:
        """A → B within one property is two intervals (US3-4)."""
        p = guesty_transport.payloads
        item = p.same_property_move(property_["parent"], property_["a"], property_["b"])
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, property_["parent"])

        rid = item["reservationId"]
        assert _ids(rid, reservation) == [
            f"{rid}::{property_['a']}",
            f"{rid}::{property_['b']}",
        ]
        assert _spans(reservation) == [
            (
                datetime.fromisoformat(s["checkIn"]),
                datetime.fromisoformat(s["checkOut"]),
            )
            for s in item["stay"]
        ]

    async def test_round_trip(
        self, guesty_transport: GuestyFakeTransport, property_: dict[str, str]
    ) -> None:
        """A → B → A is ``A``, ``B`` and ``A#2`` (FR-018)."""
        p = guesty_transport.payloads
        item = p.round_trip_move(property_["parent"], property_["a"], property_["b"])
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, property_["parent"])

        rid = item["reservationId"]
        assert _ids(rid, reservation) == [
            f"{rid}::{property_['a']}",
            f"{rid}::{property_['b']}",
            f"{rid}::{property_['a']}#2",
        ]
        assert reservation.room_ids == (property_["a"], property_["b"])

    async def test_adjacent_same_room_segments_merge(
        self, guesty_transport: GuestyFakeTransport, property_: dict[str, str]
    ) -> None:
        """Adjacent same-room segments are one interval with the max guests."""
        p = guesty_transport.payloads
        unit, parent = property_["a"], property_["parent"]
        item = p.search_item(
            stay=[
                p.stay_segment(
                    unit,
                    p.check_in_at(3),
                    p.check_in_at(5),
                    parent_listing_id=parent,
                    guests_count=2,
                ),
                p.stay_segment(
                    unit,
                    p.check_in_at(5),
                    p.check_out_at(8),
                    parent_listing_id=parent,
                    guests_count=4,
                ),
            ]
        )
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, parent)

        rid = item["reservationId"]
        assert reservation.intervals == (
            PMSOccupancyInterval(
                room_id=unit,
                ordinal=1,
                check_in=p.check_in_at(3),
                check_out=p.check_out_at(8),
                local_dates=(
                    date.fromisoformat(item["stay"][0]["checkInDateLocalized"]),
                    date.fromisoformat(item["stay"][1]["checkOutDateLocalized"]),
                ),
                guests_count=4,
            ),
        )
        assert _ids(rid, reservation) == [f"{rid}::{unit}"]

    async def test_chain_of_adjacent_segments_merges(
        self, guesty_transport: GuestyFakeTransport, property_: dict[str, str]
    ) -> None:
        """Every back-to-back segment joins the same interval, not ``#2``."""
        p = guesty_transport.payloads
        unit, parent = property_["a"], property_["parent"]
        bounds = [p.check_in_at(3), p.check_in_at(5), p.check_in_at(7)]
        item = p.search_item(
            stay=[
                p.stay_segment(unit, start, end, parent_listing_id=parent)
                for start, end in itertools.pairwise([*bounds, p.check_out_at(9)])
            ]
        )
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, parent)

        rid = item["reservationId"]
        assert _ids(rid, reservation) == [f"{rid}::{unit}"]
        assert _spans(reservation) == [(p.check_in_at(3), p.check_out_at(9))]

    async def test_same_room_gap_is_a_second_interval(
        self, guesty_transport: GuestyFakeTransport, property_: dict[str, str]
    ) -> None:
        """Same-room segments separated by a gap stay apart."""
        p = guesty_transport.payloads
        unit, parent = property_["a"], property_["parent"]
        item = p.search_item(
            stay=[
                p.stay_segment(
                    unit, p.check_in_at(3), p.check_out_at(5), parent_listing_id=parent
                ),
                p.stay_segment(
                    unit, p.check_in_at(6), p.check_out_at(8), parent_listing_id=parent
                ),
            ]
        )
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, parent)

        rid = item["reservationId"]
        assert _ids(rid, reservation) == [f"{rid}::{unit}", f"{rid}::{unit}#2"]

    async def test_sibling_only_is_not_attributed(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A tracked child never claims its sibling's stay (US3-5, FR-020)."""
        p = guesty_transport.payloads
        parent, child, sibling = p.new_id("lst"), p.new_id("lst"), p.new_id("lst")
        guesty_transport.add_listing(parent, children=[child, sibling])
        guesty_transport.add_listing(child, parent_id=parent)
        guesty_transport.v3_items = [
            p.search_item(sibling, parent_listing_id=parent),
            p.unassigned_parent(parent),
        ]
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([child])

        assert await provider.get_reservations(child) == []
        assert guesty_transport.counts["v3_search"] == 1

    @pytest.mark.parametrize(
        "bad",
        [{"checkIn": "2026-01-01T15:00:00.000Z"}, {"listingId": ""}, "6640a"],
        ids=["no_listing_id", "empty_listing_id", "not_object"],
    )
    async def test_later_segment_without_listing_aborts(
        self, guesty_transport: GuestyFakeTransport, bad: Any
    ) -> None:
        """Any segment without a usable ``listingId`` aborts the fetch."""
        p = guesty_transport.payloads
        listing = p.new_id("lst")
        guesty_transport.add_listing(listing)
        item = p.search_item(listing)
        item["stay"].append(bad)
        guesty_transport.v3_items = [p.search_item(listing), item]
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([listing])

        with pytest.raises(PMSProviderError, match="stay listing reference"):
            await provider.get_reservations(listing)

    @pytest.mark.parametrize("stay", [None, [], "6640a"])
    def test_missing_stay_aborts(
        self, guesty_v3_payloads: GuestyV3Payloads, stay: Any
    ) -> None:
        """A missing, empty or non-list ``stay`` is never guessed."""
        p = guesty_v3_payloads
        listing = p.new_id("lst")
        item = p.search_item(listing)
        if stay is None:
            del item["stay"]
        else:
            item["stay"] = stay

        with pytest.raises(PMSProviderError, match="stay listing reference"):
            map_search_item(item, listing, frozenset({listing}))


def _shape(p: GuestyV3Payloads, kind: str) -> tuple[dict[str, Any], str, set[str]]:
    """Return a search item, its owning scope root and unassigned parents."""
    parent, unit_a, unit_b = p.new_id("lst"), p.new_id("lst"), p.new_id("lst")
    if kind == "single":
        return p.search_item(unit_a), unit_a, set()
    if kind == "multi_unit_assigned":
        return p.same_property_move(parent, unit_a, unit_b), parent, set()
    if kind == "unassigned_parent":
        return p.unassigned_parent(parent), parent, {parent}
    return p.round_trip_move(parent, unit_a, unit_b), parent, set()


class TestNormaliseByIds:
    """By-IDs items normalise to the search shape (T032, FR-005)."""

    @pytest.mark.parametrize(
        "kind",
        ["single", "multi_unit_assigned", "unassigned_parent", "round_trip"],
    )
    def test_same_identity_from_either_endpoint(
        self, guesty_v3_payloads: GuestyV3Payloads, kind: str
    ) -> None:
        """Byte-identical IDs and rooms; by-IDs intervals carry dates only."""
        item, root, unassigned = _shape(guesty_v3_payloads, kind)
        scope = frozenset(
            {root}
            | {s["listingId"] for s in item["stay"]}
            | {s.get("parentListingId") or root for s in item["stay"]}
        )

        searched = map_search_item(item, root, scope)
        normalised = normalise_by_ids_item(
            guesty_v3_payloads.as_by_ids(item, unassigned=unassigned)
        )
        assert normalised is not None
        fetched = map_by_ids_item(normalised, root, scope)

        assert searched is not None
        assert fetched is not None
        assert fetched.pms_booking_id == searched.pms_booking_id
        assert [
            (i.room_id, i.ordinal, i.booking_suffix, i.local_dates)
            for i in fetched.intervals
        ] == [
            (i.room_id, i.ordinal, i.booking_suffix, i.local_dates)
            for i in searched.intervals
        ]
        assert all(
            i.check_in is None and i.check_out is None for i in fetched.intervals
        )

    def test_field_mapping(self, guesty_v3_payloads: GuestyV3Payloads) -> None:
        """``_id``, ``unitId ?? unitTypeId`` and the parent only when it differs."""
        p = guesty_v3_payloads
        rid, parent, unit, single = (
            p.new_id("res"),
            p.new_id("lst"),
            p.new_id("lst"),
            p.new_id("lst"),
        )
        raw = p.by_ids_item(
            rid,
            [
                p.by_ids_segment(single, "2026-01-01", "2026-01-03", unit_id=single),
                p.by_ids_segment(parent, "2026-01-03", "2026-01-05", unit_id=unit),
                p.by_ids_segment(parent, "2026-01-05", "2026-01-07"),
            ],
            status="confirmed",
            guest_id="6630synthetic",
        )

        item = normalise_by_ids_item(raw)

        assert item is not None
        assert item["reservationId"] == rid
        assert item["status"] == "confirmed"
        assert item["guestId"] == "6630synthetic"
        assert [(s["listingId"], s.get("parentListingId")) for s in item["stay"]] == [
            (single, None),
            (unit, parent),
            (parent, None),
        ]
        assert [
            (s["checkInDateLocalized"], s["checkOutDateLocalized"])
            for s in item["stay"]
        ] == [
            ("2026-01-01", "2026-01-03"),
            ("2026-01-03", "2026-01-05"),
            ("2026-01-05", "2026-01-07"),
        ]
        assert all("checkIn" not in s and "checkOut" not in s for s in item["stay"])

    @pytest.mark.parametrize("gap", [False, True], ids=["utc_adjacent", "same_day_gap"])
    def test_equal_same_room_boundary_dates_are_ambiguous(
        self, guesty_v3_payloads: GuestyV3Payloads, gap: bool
    ) -> None:
        """Without instants, one shared local date cannot be resolved."""
        p = guesty_v3_payloads
        room, other = p.new_id("lst"), p.new_id("lst")
        second_in = p.check_in_at(7) if gap else p.check_out_at(7)
        item = p.search_item(
            stay=[
                p.stay_segment(other, p.check_in_at(4), p.check_out_at(5)),
                p.stay_segment(room, p.check_out_at(5), p.check_out_at(7)),
                p.stay_segment(room, second_in, p.check_out_at(9)),
            ]
        )

        normalised = normalise_by_ids_item(p.as_by_ids(item))

        assert normalised is not None
        assert ambiguous_rooms(normalised) == {room}

    def test_round_trip_is_not_ambiguous(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """A → B → A has no consecutive same-room segments."""
        item, _root, _unassigned = _shape(guesty_v3_payloads, "round_trip")

        normalised = normalise_by_ids_item(guesty_v3_payloads.as_by_ids(item))

        assert normalised is not None
        assert ambiguous_rooms(normalised) == set()

    @pytest.mark.parametrize(
        "raw",
        [
            "not-an-object",
            {"status": "confirmed", "stay": [{"unitTypeId": "6640a"}]},
            {"_id": "", "status": "confirmed", "stay": [{"unitTypeId": "6640a"}]},
            {"_id": "6650a", "status": "confirmed"},
            {"_id": "6650a", "status": "confirmed", "stay": []},
            {"_id": "6650a", "status": "confirmed", "stay": ["6640a"]},
            {
                "_id": "6650a",
                "status": "confirmed",
                "stay": [{"checkInDateLocalized": "2026-01-01"}],
            },
        ],
        ids=[
            "not_object",
            "no_id",
            "empty_id",
            "no_stay",
            "empty_stay",
            "segment_not_object",
            "segment_without_unit",
        ],
    )
    def test_invalid_items(self, raw: Any) -> None:
        """An unattributable item is invalid, never guessed."""
        assert normalise_by_ids_item(raw) is None


_V3_LOGGER = "src.providers.guesty.v3_mapping"


class TestStatusPolicy:
    """The full V3 status vocabulary maps per research R7 (T043, US4)."""

    async def _fetch(
        self, transport: GuestyFakeTransport, *statuses: str
    ) -> tuple[list[Any], str]:
        """Sync one listing holding one stay per status; return the DTOs."""
        p = transport.payloads
        listing = p.new_id("lst")
        transport.add_listing(listing)
        transport.v3_items = [
            p.search_item(listing, status=status, check_in=2 + n, check_out=4 + n)
            for n, status in enumerate(statuses)
        ]
        provider = transport.provider(reservations_api="v3")
        provider.begin_cycle([listing])
        return await provider.get_reservations(listing), listing

    @pytest.mark.parametrize(
        ("v3_status", "expected"),
        [
            ("completed", "checked_out"),
            ("Completed", "checked_out"),
            ("canceled", "cancelled"),
            ("cancelled", "cancelled"),
            ("CANCELED", "cancelled"),
            ("Cancelled", "cancelled"),
            ("CONFIRMED", "confirmed"),
        ],
    )
    async def test_normalised_statuses(
        self,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
        v3_status: str,
        expected: str,
    ) -> None:
        """``completed`` exports as ``checked_out``; FR-024 is case-blind."""
        with caplog.at_level(logging.DEBUG, logger=_V3_LOGGER):
            (reservation,), _ = await self._fetch(guesty_transport, v3_status)

        assert reservation.status == expected
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    @pytest.mark.parametrize("v3_status", ["inquiry", "reserved", "Inquiry"])
    async def test_excluded_statuses_are_dropped(
        self, guesty_transport: GuestyFakeTransport, v3_status: str
    ) -> None:
        """``inquiry``/``reserved`` stay out of the snapshot (FR-022)."""
        reservations, _ = await self._fetch(guesty_transport, v3_status)

        assert reservations == []

    @pytest.mark.parametrize(
        "v3_status", ["expired", "closed", "declined", "awaiting_payment"]
    )
    async def test_recognised_non_export_statuses_are_quiet(
        self,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
        v3_status: str,
    ) -> None:
        """Stored verbatim (withheld) with only a DEBUG log."""
        with caplog.at_level(logging.DEBUG, logger=_V3_LOGGER):
            (reservation,), _ = await self._fetch(guesty_transport, v3_status)

        assert reservation.status == v3_status
        levels = {r.levelno for r in caplog.records if v3_status in r.getMessage()}
        assert levels == {logging.DEBUG}

    async def test_unknown_status_is_verbatim_with_a_warning(
        self,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An unmapped status is withheld, never guessed or dropped."""
        with caplog.at_level(logging.DEBUG, logger=_V3_LOGGER):
            (reservation,), _ = await self._fetch(guesty_transport, "synthetic_new")

        assert reservation.status == "synthetic_new"
        (record,) = [r for r in caplog.records if "synthetic_new" in r.getMessage()]
        assert record.levelno == logging.WARNING
        assert reservation.pms_booking_id in record.getMessage()

    @pytest.mark.parametrize("v3_status", ["alteration", "ALTERATION"])
    async def test_alteration_is_a_quiet_hold_status(
        self,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
        v3_status: str,
    ) -> None:
        """``alteration`` reaches the sync as a hold status, at DEBUG."""
        with caplog.at_level(logging.DEBUG, logger=_V3_LOGGER):
            (reservation,), _ = await self._fetch(guesty_transport, v3_status)

        assert reservation.status == "alteration"
        assert reservation.status in HOLD_STATUSES
        records = [r for r in caplog.records if "alteration" in r.getMessage()]
        assert records
        assert {r.levelno for r in records} == {logging.DEBUG}

    @pytest.mark.parametrize("v3_status", ["alteration", "completed"])
    async def test_health_guard_counts_them_classified(
        self, guesty_transport: GuestyFakeTransport, v3_status: str
    ) -> None:
        """An all-``alteration``/``completed`` window never trips the guard."""
        reservations, _ = await self._fetch(guesty_transport, *[v3_status] * 5)

        assert len(reservations) == 5

    async def test_health_guard_thresholds_are_unchanged(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Over half of at least four unclassified aborts (FR-025)."""
        unknown = "synthetic_new"
        majority = ["confirmed", unknown, unknown, unknown]
        half = ["confirmed", "alteration", unknown, unknown]
        small = [unknown, unknown, unknown]

        with pytest.raises(PMSProviderError, match="unrecognised"):
            await self._fetch(guesty_transport, *majority)
        assert len((await self._fetch(guesty_transport, *half))[0]) == 4
        assert len((await self._fetch(guesty_transport, *small))[0]) == 3


def _rebuilt(item: dict[str, Any]) -> dict[str, Any]:
    """Return *item* with every nested key order reversed."""

    def reverse(value: Any) -> Any:
        """Rebuild *value* with reversed dict key order."""
        if isinstance(value, dict):
            return {k: reverse(value[k]) for k in reversed(list(value))}
        if isinstance(value, list):
            return [reverse(v) for v in value]
        return value

    rebuilt: dict[str, Any] = reverse(item)
    return rebuilt


class TestChangeKey:
    """The enrichment reuse key (T048, research R9)."""

    @staticmethod
    def _item(p: GuestyV3Payloads) -> dict[str, Any]:
        """Build a multi-unit item with two inline custom fields."""
        unit, parent = p.new_id("lst"), p.new_id("lst")
        return p.search_item(
            unit,
            parent_listing_id=parent,
            custom_fields=p.custom_fields({"door_code": "1234", "gate": "77"}),
        )

    def test_is_hex_sha256_and_stable_across_key_order(
        self, guesty_v3_payloads: GuestyV3Payloads
    ) -> None:
        """Canonical JSON: key order, and custom-field order, do not matter."""
        item = self._item(guesty_v3_payloads)
        reordered = _rebuilt(item)
        reordered["customFields"] = list(reversed(reordered["customFields"]))

        key = change_key(item)

        assert len(key) == 64
        assert int(key, 16) >= 0
        assert change_key(reordered) == key
        assert change_key(copy.deepcopy(item)) == key

    @pytest.mark.parametrize(
        ("field", "mutate"),
        [
            ("status", lambda i: i.update(status="canceled")),
            ("guestId", lambda i: i.update(guestId="6630" + "f" * 20)),
            ("checkIn", lambda i: i.update(checkIn="2030-01-01T15:00:00.000Z")),
            ("checkOut", lambda i: i.update(checkOut="2030-01-09T10:00:00.000Z")),
            (
                "stay.listingId",
                lambda i: i["stay"][0].update(listingId="6640" + "e" * 20),
            ),
            (
                "stay.parentListingId",
                lambda i: i["stay"][0].update(parentListingId="6640" + "d" * 20),
            ),
            (
                "stay.checkIn",
                lambda i: i["stay"][0].update(checkIn="2030-01-02T15:00:00.000Z"),
            ),
            (
                "stay.checkOut",
                lambda i: i["stay"][0].update(checkOut="2030-01-08T10:00:00.000Z"),
            ),
            ("stay.guestsCount", lambda i: i["stay"][0].update(guestsCount=5)),
            ("stay.added", lambda i: i["stay"].append(dict(i["stay"][0]))),
            ("customFields.value", lambda i: i["customFields"][0].update(value="9")),
            ("customFields.removed", lambda i: i.pop("customFields")),
            ("notes", lambda i: i["notes"].update(other="Changed synthetic note")),
            ("source", lambda i: i.update(source="airbnb")),
            ("confirmationCode", lambda i: i.update(confirmationCode="SYN-OTHER")),
        ],
    )
    def test_changes_with_each_included_field(
        self, guesty_v3_payloads: GuestyV3Payloads, field: str, mutate: Any
    ) -> None:
        """Every field the sync consumes feeds the key."""
        item = self._item(guesty_v3_payloads)
        changed = copy.deepcopy(item)
        mutate(changed)

        assert change_key(changed) != change_key(item), field

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda i: i.update(money={"totalPaid": 100, "currency": "USD"}),
            lambda i: i.update(financials={"balanceDue": 5}),
            lambda i: i.update(createdAt="2026-01-01T00:00:00.000Z"),
            lambda i: i.update(checkInDateLocalized="2030-01-01"),
            lambda i: i["stay"][0].update(checkInDateLocalized="2030-01-01"),
            lambda i: i["stay"][0].update(unitTypeName="Synthetic type"),
        ],
        ids=[
            "money",
            "financials",
            "createdAt",
            "localized",
            "stay-localized",
            "stay-extra",
        ],
    )
    def test_ignores_other_fields(
        self, guesty_v3_payloads: GuestyV3Payloads, mutate: Any
    ) -> None:
        """Financial blocks and other unconsumed fields do not change it."""
        item = self._item(guesty_v3_payloads)
        changed = copy.deepcopy(item)
        mutate(changed)

        assert change_key(changed) == change_key(item)

    async def test_mapped_reservation_carries_it(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """The search mapper sets ``change_key`` from the raw item."""
        p = guesty_transport.payloads
        listing = p.new_id("lst")
        guesty_transport.add_listing(listing)
        item = p.search_item(listing)
        guesty_transport.v3_items = [item]

        reservation = await _fetch_one(guesty_transport, listing)

        assert reservation.change_key == change_key(item)


class TestRetiredDescriptionFields:
    """Payment totals are retired for Guesty V3 only (T060, FR-036)."""

    MONEY = frozenset({"money_totalPaid", "money_balanceDue", "money_currency"})

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("api", "retired"), [("v3", True), ("legacy", False)])
    async def test_provider_exposes_them_in_v3_mode(
        self, guesty_transport: GuestyFakeTransport, api: str, retired: bool
    ) -> None:
        """The provider reports the retired set only when reading V3."""
        provider = guesty_transport.provider(reservations_api=api)
        try:
            fields = provider.retired_description_fields
        finally:
            await provider.aclose()

        assert set(fields) == (self.MONEY if retired else set())
        assert all(isinstance(reason, str) and reason for reason in fields.values())

    @pytest.mark.parametrize(
        ("pms_type", "api", "retired"),
        [
            ("guesty", "v3", True),
            ("guesty", "legacy", False),
            ("cloudbeds", "v3", False),
            ("cloudbeds", "legacy", False),
        ],
    )
    def test_static_lookup(self, pms_type: str, api: str, retired: bool) -> None:
        """The lookup needs no provider instance or network call."""
        from src.providers.guesty.description_fields import retired_fields_for

        assert set(retired_fields_for(pms_type, api)) == (
            self.MONEY if retired else set()
        )


class TestV3DiagnosticStatuses:
    """DEBUG diagnostics log only known status values (FR-043)."""

    def test_unknown_or_malformed_status_is_logged_as_unknown(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test a status that is not a known enum value is never echoed."""
        from src.providers.guesty.v3_diagnostics import log_search_result

        stay = [{"listingId": "lst-1"}]
        items = [
            {"reservationId": "r1", "status": "Confirmed", "stay": stay},
            {"reservationId": "r2", "status": {"guest": "Sentinel Name"}, "stay": stay},
            {"reservationId": "r3", "status": "sentinel-free-text", "stay": stay},
        ]
        with caplog.at_level(logging.DEBUG, logger="src.providers.guesty"):
            log_search_result(items, 0)

        assert "Sentinel" not in caplog.text
        assert "sentinel-free-text" not in caplog.text
        assert "statuses={'confirmed': 1, 'unknown': 2}" in caplog.text
        assert "Guesty V3 reservation r2: status=unknown" in caplog.text
        assert "Guesty V3 reservation r1: status=confirmed" in caplog.text
