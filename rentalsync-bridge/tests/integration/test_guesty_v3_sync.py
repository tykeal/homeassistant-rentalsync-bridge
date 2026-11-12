# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for syncing through the Guesty V3 source (spec 004 US1-US4)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from src.models.booking import Booking
from src.models.listing import Listing
from src.providers.base import AbsenceVerdict
from src.providers.guesty import reservations_v3, v3_search
from src.repositories.booking_repository import BookingRepository
from src.services.calendar_service import CalendarCache
from src.services.sync_service import SyncService, SyncServiceError

from tests.conftest import (
    SEARCH_PAGE_FAULTS,
    GuestyFakeTransport,
    make_booking,
    make_listing,
    make_room,
)

DOOR_CODE = "door_code"


class _Fixture:
    """Tracked listings with rooms, and one stay in the first listing."""

    def __init__(
        self,
        session: AsyncSession,
        factory: async_sessionmaker[AsyncSession],
        transport: GuestyFakeTransport,
    ) -> None:
        """Hold the collaborators the helpers below share."""
        self.session = session
        self.factory = factory
        self.transport = transport
        self.listing: Listing
        self.room_id: int
        self.rooms: dict[str, int] = {}
        self.item: dict[str, Any]

    async def add_tracked(self, *, children: list[str] | None = None) -> Listing:
        """Create a tracked listing with one room per unit (or itself)."""
        pms_id = self.transport.payloads.new_id("lst")
        self.transport.add_listing(pms_id, children=children)
        listing = make_listing(pms_id=pms_id, slug=f"v3-{pms_id[-6:]}", timezone="UTC")
        self.session.add(listing)
        await self.session.commit()
        await self.session.refresh(listing)
        for room_pms_id in children or [pms_id]:
            room = make_room(
                listing_id=listing.id,
                pms_room_id=room_pms_id,
                slug=f"r-{room_pms_id[-6:]}",
            )
            self.session.add(room)
            await self.session.commit()
            await self.session.refresh(room)
            self.rooms[room_pms_id] = room.id
        return listing

    async def setup(self, *, custom_fields: Any = None, **item: Any) -> None:
        """Create the listing, its room and the account's single stay."""
        p = self.transport.payloads
        self.listing = await self.add_tracked()
        pms_id = self.listing.pms_id
        self.room_id = self.rooms[pms_id]
        item.setdefault("check_in", 2)
        item.setdefault("check_out", 5)
        self.item = p.search_item(pms_id, custom_fields=custom_fields, **item)
        self.transport.v3_items = [self.item]
        guest_id = self.item["guestId"]
        self.transport.guests[guest_id] = p.guests_crud_body(guest_id)

    @property
    def booking_id(self) -> str:
        """Return the stay's (single-interval) booking ID."""
        return f"{self.item['reservationId']}::{self.listing.pms_id}"

    async def store(
        self,
        *,
        pms_booking_id: str | None = None,
        custom_data: dict[str, Any] | None = None,
        enrichment_data: dict[str, Any] | None = None,
        listing: Listing | None = None,
        room: str | None = None,
        check_in: int = 2,
        check_out: int = 5,
        guest_phone_last4: str | None = None,
    ) -> Booking:
        """Store a previously synced booking (default: the stay's)."""
        p = self.transport.payloads
        listing = listing or self.listing
        booking = make_booking(
            listing_id=listing.id,
            room_id=self.rooms[room or listing.pms_id],
            pms_booking_id=pms_booking_id or self.booking_id,
            guest_name="Synthetic Guest",
            guest_phone_last4=guest_phone_last4,
            check_in_date=p.check_in_at(check_in),
            check_out_date=p.check_out_at(check_out),
            custom_data=custom_data,
        )
        booking.enrichment_data = enrichment_data
        self.session.add(booking)
        await self.session.commit()
        await self.session.refresh(booking)
        return booking

    async def sync_with(self, provider: Any, listing: Listing) -> dict[str, int]:
        """Sync one listing through an already-begun provider cycle."""
        service = SyncService(
            self.session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=self.factory,
        )
        return await service.sync_listing(listing, provider)

    async def sync(self, listing: Listing | None = None) -> dict[str, int]:
        """Run one V3 sync cycle of a single listing (default: the first)."""
        listing = listing or self.listing
        provider = self.transport.provider(reservations_api="v3")
        provider.begin_cycle([listing.pms_id])
        return await self.sync_with(provider, listing)

    async def bookings(self, listing: Listing | None = None) -> dict[str, Booking]:
        """Return a listing's stored bookings by booking ID, refreshed."""
        listing = listing or self.listing
        rows = await BookingRepository(self.session).get_for_listing(listing.id)
        for row in rows:
            await self.session.refresh(row)
        return {row.pms_booking_id: row for row in rows}

    async def feed(self) -> dict[str, dict[str, Any]]:
        """Render the first listing's feed and index its events by UID."""
        from src.services.calendar_service import CalendarService

        repo = BookingRepository(self.session)
        bookings = list(await repo.get_confirmed_for_listing(self.listing.id))
        for booking in bookings:
            await self.session.refresh(booking)
        return _events(
            CalendarService(cache=CalendarCache(ttl_seconds=0)).generate_ical(
                self.listing, bookings
            )
        )


@pytest.fixture
async def v3(
    async_session: AsyncSession,
    async_session_factory: async_sessionmaker[AsyncSession],
    guesty_transport: GuestyFakeTransport,
) -> _Fixture:
    """A V3 sync harness over the counting fake transport."""
    return _Fixture(async_session, async_session_factory, guesty_transport)


class TestInlineCustomFieldEnrichment:
    """Inline custom fields replace per-reservation calls (T020, R8)."""

    async def test_present_makes_no_call(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Inline values are used directly."""
        p = guesty_transport.payloads
        await v3.setup(custom_fields=p.custom_fields({DOOR_CODE: "4242"}))

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.custom_data[DOOR_CODE] == "4242"
        assert booking.enrichment_data[DOOR_CODE] == "4242"
        assert guesty_transport.counts["custom_fields"] == 0

    async def test_absent_with_empty_enrichment_makes_no_call(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A stored successful empty read is confirmed without a call."""
        await v3.setup()
        await v3.store(enrichment_data={})

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert DOOR_CODE not in (booking.custom_data or {})
        assert guesty_transport.counts["custom_fields"] == 0

    async def test_absent_never_stored_makes_no_call(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A new stay has nothing published, so absence is an empty read."""
        await v3.setup()

        await v3.sync()

        assert v3.booking_id in await v3.bookings()
        assert guesty_transport.counts["custom_fields"] == 0

    async def test_absent_with_stored_values_confirms_once(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Vanished values are confirmed by one call, whose answer wins."""
        await v3.setup()
        guesty_transport.custom_fields[v3.item["reservationId"]] = [
            {"fieldId": DOOR_CODE, "value": "9999"}
        ]
        await v3.store(
            custom_data={DOOR_CODE: "1234"}, enrichment_data={DOOR_CODE: "1234"}
        )

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.custom_data[DOOR_CODE] == "9999"
        assert guesty_transport.counts["custom_fields"] == 1

    async def test_absent_with_stored_values_keeps_last_good_on_failure(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A failed confirmation keeps the published door code (FR-032)."""
        await v3.setup()
        guesty_transport.fail("custom_fields", 500)
        await v3.store(
            custom_data={DOOR_CODE: "1234"}, enrichment_data={DOOR_CODE: "1234"}
        )

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.custom_data[DOOR_CODE] == "1234"
        assert guesty_transport.counts["custom_fields"] == 1

    async def test_absent_with_null_enrichment_confirms_once(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Unknown provenance is confirmed by one call."""
        await v3.setup()
        guesty_transport.custom_fields[v3.item["reservationId"]] = [
            {"fieldId": DOOR_CODE, "value": "9999"}
        ]
        await v3.store(custom_data={DOOR_CODE: "1234"}, enrichment_data=None)

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.custom_data[DOOR_CODE] == "9999"
        assert guesty_transport.counts["custom_fields"] == 1

    async def test_absent_with_null_enrichment_bootstraps_on_failure(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A failed confirmation over a pre-upgrade row recovers its code."""
        await v3.setup()
        guesty_transport.fail("custom_fields", 500)
        await v3.store(custom_data={DOOR_CODE: "1234"}, enrichment_data=None)

        await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.custom_data[DOOR_CODE] == "1234"
        assert guesty_transport.counts["custom_fields"] == 1

    @pytest.mark.parametrize(
        "malformed",
        ["not-a-list", [{"value": "0000"}], [{"fieldId": DOOR_CODE}]],
        ids=["string", "element", "no-value"],
    )
    @pytest.mark.parametrize("enrichment", [{DOOR_CODE: "1234"}, None])
    async def test_malformed_keeps_last_good(
        self,
        v3: _Fixture,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
        malformed: Any,
        enrichment: dict[str, Any] | None,
    ) -> None:
        """A malformed block warns, keeps the door code and still syncs."""
        await v3.setup(custom_fields=malformed)
        await v3.store(custom_data={DOOR_CODE: "1234"}, enrichment_data=enrichment)

        with caplog.at_level(logging.WARNING):
            await v3.sync()

        booking = (await v3.bookings())[v3.booking_id]
        assert booking.status == "confirmed"
        assert booking.custom_data[DOOR_CODE] == "1234"
        assert guesty_transport.counts["custom_fields"] == 0
        assert any(
            record.levelno == logging.WARNING
            and v3.item["reservationId"] in record.getMessage()
            for record in caplog.records
        )
        assert v3.listing.last_sync_error is None


class TestIdentityVerification:
    """A re-keyed reservation stops the sync (T021, R14, FR-006)."""

    async def test_rekeyed_reservation_aborts(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Same confirmation code, new ID, old ID gone: nothing changes."""
        await v3.setup()
        old_id = guesty_transport.payloads.new_id("res")
        stored = await v3.store(
            pms_booking_id=f"{old_id}::{v3.listing.pms_id}",
            custom_data={"confirmationCode": v3.item["confirmationCode"]},
        )

        with pytest.raises(SyncServiceError) as excinfo:
            await v3.sync()

        message = str(excinfo.value)
        assert old_id in message
        assert v3.item["reservationId"] in message
        assert "guesty_reservations_api" in message
        bookings = await v3.bookings()
        assert list(bookings) == [stored.pms_booking_id]
        assert bookings[stored.pms_booking_id].status == "confirmed"
        await v3.session.refresh(v3.listing)
        assert v3.listing.last_sync_error

    async def test_matching_ids_log_matched_count(
        self,
        v3: _Fixture,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Stored IDs found under their codes are counted at INFO."""
        await v3.setup()
        await v3.store(custom_data={"confirmationCode": v3.item["confirmationCode"]})

        with caplog.at_level(logging.INFO):
            await v3.sync()

        assert any(
            record.levelno == logging.INFO
            and "Identity check" in record.getMessage()
            and "1 stored reservation" in record.getMessage()
            for record in caplog.records
        )
        assert (await v3.bookings())[v3.booking_id].status == "confirmed"


def _calls(transport: GuestyFakeTransport, identifier: str) -> int:
    """Count the requests whose path names *identifier*."""
    return sum(identifier in request.path for request in transport.requests)


def _as_utc(value: datetime) -> datetime:
    """Return a stored (SQLite-naive UTC) datetime as aware UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class _Moved:
    """A stored stay of the first listing, re-arranged by the tests."""

    def __init__(self, v3: _Fixture) -> None:
        """Register an untracked property and reserve a reservation ID."""
        self.v3 = v3
        self.p = v3.transport.payloads
        self.untracked = self.p.new_id("lst")
        v3.transport.add_listing(self.untracked)
        self.rid = self.p.new_id("res")
        self.guest_id = self.p.new_id("gst")

    @property
    def booking_id(self) -> str:
        """Return the stored row's booking ID."""
        return f"{self.rid}::{self.v3.listing.pms_id}"

    async def store(self, **kwargs: Any) -> Booking:
        """Store the row as last synced (days 2-5 in the first listing)."""
        kwargs.setdefault(
            "custom_data",
            {
                DOOR_CODE: "4321",
                "guest_phone": "+15555550100",
                "guest_phone_last4": "0100",
            },
        )
        kwargs.setdefault(
            "enrichment_data",
            {
                DOOR_CODE: "4321",
                "guest_phone": "+15555550100",
                "guest_phone_last4": "0100",
                "_guest_source_id": self.guest_id,
            },
        )
        kwargs.setdefault("guest_phone_last4", "0100")
        kwargs.setdefault("pms_booking_id", self.booking_id)
        return await self.v3.store(**kwargs)

    def rearrange(self, *, start: int = 0, **kwargs: Any) -> dict[str, Any]:
        """Move the first segment to the untracked property (by-IDs only)."""
        kwargs.setdefault("guest_id", self.guest_id)
        item = self.p.rearranged_into_untracked(
            self.untracked,
            self.v3.listing.pms_id,
            start=start,
            reservation_id=self.rid,
            **kwargs,
        )
        self.v3.transport.v3_items.append(item)
        return item

    def by_ids_only(self, stay: list[dict[str, Any]], **kwargs: Any) -> None:
        """Serve an explicit by-IDs item the search never returns."""
        kwargs.setdefault("guest_id", self.guest_id)
        self.v3.transport.by_ids_overrides[self.rid] = self.p.by_ids_item(
            self.rid, stay, **kwargs
        )

    def local(self, offset: int) -> str:
        """Return the local date *offset* days from today."""
        return self.p.day(offset).date().isoformat()


@pytest.fixture
async def moved(v3: _Fixture) -> _Moved:
    """The V3 harness plus a stored stay ready to be re-arranged."""
    await v3.setup()
    return _Moved(v3)


class TestInconsistentAbsence:
    """A live stay the search should have returned aborts (T033, US2-5)."""

    async def test_vanished_live_stay_aborts_only_its_listing(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Last-good kept, actionable error; the other listing syncs."""
        p = guesty_transport.payloads
        await v3.setup()
        await v3.store()
        vanished = p.search_item(v3.listing.pms_id, check_in=3, check_out=6)
        rid = vanished["reservationId"]
        guesty_transport.by_ids_overrides[rid] = p.as_by_ids(vanished)
        vanished_id = f"{rid}::{v3.listing.pms_id}"
        await v3.store(pms_booking_id=vanished_id, check_in=3, check_out=6)
        other = await v3.add_tracked()
        guesty_transport.v3_items.append(p.search_item(other.pms_id))
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([v3.listing.pms_id, other.pms_id])

        with pytest.raises(SyncServiceError) as excinfo:
            await v3.sync_with(provider, v3.listing)
        await v3.sync_with(provider, other)

        message = str(excinfo.value)
        assert rid in message
        assert "guesty_reservations_api" in message
        bookings = await v3.bookings()
        assert {k: b.status for k, b in bookings.items()} == {
            v3.booking_id: "confirmed",
            vanished_id: "confirmed",
        }
        await v3.session.refresh(v3.listing)
        assert v3.listing.last_sync_error
        assert len(await v3.bookings(other)) == 1
        await v3.session.refresh(other)
        assert other.last_sync_error is None
        assert guesty_transport.counts["v3_search"] == 1


class TestReattribution:
    """A stay re-arranged to start elsewhere keeps its room (T033, US3-6)."""

    async def test_unchanged_dates_keep_everything(
        self, moved: _Moved, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Instants, custom fields, guest details, UID and Booking ID stay."""
        stored = await moved.store()
        stored_dates = (stored.check_in_date, stored.check_out_date)
        moved.rearrange()
        before = await moved.v3.feed()

        await moved.v3.sync()

        booking = (await moved.v3.bookings())[moved.booking_id]
        assert booking.status == "confirmed"
        assert (booking.check_in_date, booking.check_out_date) == stored_dates
        assert booking.custom_data[DOOR_CODE] == "4321"
        assert booking.guest_phone_last4 == "0100"
        assert booking.guest_name == "Synthetic Guest"
        after = await moved.v3.feed()
        (uid,) = [u for u, e in before.items() if moved.rid in str(e["Booking ID"])]
        assert after[uid] == before[uid]
        assert _calls(guesty_transport, moved.rid) == 0
        assert _calls(guesty_transport, moved.guest_id) == 0

    async def test_changed_dates_reanchor_at_the_stored_time_of_day(
        self, moved: _Moved
    ) -> None:
        """A moved local date keeps the stored local check-in/out times."""
        await moved.store()
        moved.rearrange(start=1, status="checked_in")

        await moved.v3.sync()

        booking = (await moved.v3.bookings())[moved.booking_id]
        assert booking.status == "checked_in"
        assert _as_utc(booking.check_in_date) == moved.p.check_in_at(3)
        assert _as_utc(booking.check_out_date) == moved.p.check_out_at(6)
        assert booking.custom_data[DOOR_CODE] == "4321"

    async def test_three_cycles_never_abort(
        self, moved: _Moved, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Stays exported; one by-IDs call per cycle."""
        await moved.store()
        moved.rearrange()

        for _ in range(3):
            await moved.v3.sync()
            assert moved.booking_id in {
                b.pms_booking_id
                for b in await BookingRepository(
                    moved.v3.session
                ).get_confirmed_for_listing(moved.v3.listing.id)
            }

        assert guesty_transport.counts["v3_by_ids"] == 3

    async def test_no_tracked_segment_left_retires(self, moved: _Moved) -> None:
        """Re-arranged wholly into untracked properties: gone."""
        await moved.store()
        moved.v3.transport.v3_items.append(
            moved.p.search_item(moved.untracked, reservation_id=moved.rid)
        )

        await moved.v3.sync()

        assert (await moved.v3.bookings())[moved.booking_id].status == "cancelled"

    async def test_residual_gap_retires_old_room_and_logs_new(
        self,
        v3: _Fixture,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """U → B: A's row retires, B is not created, INFO names it."""
        p = guesty_transport.payloads
        unit_a, unit_b, untracked = p.new_id("lst"), p.new_id("lst"), p.new_id("lst")
        guesty_transport.add_listing(untracked)
        v3.listing = await v3.add_tracked(children=[unit_a, unit_b])
        parent = v3.listing.pms_id
        guesty_transport.v3_items = [p.search_item(unit_a, parent_listing_id=parent)]
        rid = p.new_id("res")
        await v3.store(pms_booking_id=f"{rid}::{unit_a}", room=unit_a)
        guesty_transport.v3_items.append(
            p.rearranged_into_untracked(untracked, unit_b, reservation_id=rid)
        )

        with caplog.at_level(logging.INFO):
            await v3.sync()

        bookings = await v3.bookings()
        assert bookings[f"{rid}::{unit_a}"].status == "cancelled"
        assert f"{rid}::{unit_b}" not in bookings
        assert any(
            record.levelno == logging.INFO
            and rid in record.getMessage()
            and unit_b in record.getMessage()
            for record in caplog.records
        )

    @pytest.mark.parametrize("gap", [False, True], ids=["utc_adjacent", "same_day_gap"])
    async def test_ambiguous_same_room_boundary_keeps_rows(
        self, moved: _Moved, gap: bool
    ) -> None:
        """Same-room segments meeting on one local date retire no #n row."""
        p, room = moved.p, moved.v3.listing.pms_id
        await moved.store(check_in=2, check_out=4)
        await moved.store(
            pms_booking_id=f"{moved.booking_id}#2", check_in=4, check_out=6
        )
        second_in = p.check_in_at(4) if gap else p.check_out_at(4)
        moved.v3.transport.v3_items.append(
            p.search_item(
                reservation_id=moved.rid,
                guest_id=moved.guest_id,
                stay=[
                    p.stay_segment(
                        moved.untracked, p.check_in_at(1), p.check_out_at(2)
                    ),
                    p.stay_segment(room, p.check_out_at(2), p.check_out_at(4)),
                    p.stay_segment(room, second_in, p.check_out_at(6)),
                ],
            )
        )

        await moved.v3.sync()

        bookings = await moved.v3.bookings()
        assert bookings[moved.booking_id].status == "confirmed"
        assert bookings[f"{moved.booking_id}#2"].status == "confirmed"

    async def test_invalid_reanchored_row_changes_nothing(self, moved: _Moved) -> None:
        """A same-day assignment cannot be re-anchored: nothing is written."""
        stored = await moved.store()
        stored_dates = (stored.check_in_date, stored.check_out_date)
        room = moved.v3.listing.pms_id
        moved.by_ids_only(
            [
                moved.p.by_ids_segment(
                    moved.untracked,
                    moved.local(3),
                    moved.local(5),
                    unit_id=moved.untracked,
                ),
                moved.p.by_ids_segment(
                    room, moved.local(5), moved.local(5), unit_id=room
                ),
            ]
        )

        await moved.v3.sync()

        booking = (await moved.v3.bookings())[moved.booking_id]
        assert booking.status == "confirmed"
        assert (booking.check_in_date, booking.check_out_date) == stored_dates

    async def test_reassigned_guest_publishes_only_the_new_guest(
        self, moved: _Moved, guesty_transport: GuestyFakeTransport
    ) -> None:
        """No previous-guest contact, last-4 or name; one lookup (FR-031)."""
        await moved.store(
            enrichment_data={
                DOOR_CODE: "4321",
                "guest_phone": "+15555550100",
                "guest_phone_last4": "0100",
                "_guest_source_id": moved.guest_id,
                "_guest_name": "Synthetic Guest",
                "_change_key": "stale",
            }
        )
        new_guest = moved.p.new_id("gst")
        guesty_transport.guests[new_guest] = moved.p.guests_crud_body(
            new_guest, full_name="Synthetic Other", phone="+15555550199"
        )
        moved.rearrange(guest_id=new_guest)

        await moved.v3.sync()

        booking = (await moved.v3.bookings())[moved.booking_id]
        assert booking.guest_phone_last4 == "0199"
        assert booking.guest_name == "Synthetic Other"
        assert booking.custom_data[DOOR_CODE] == "4321"
        assert "0100" not in str(booking.custom_data)
        assert "0100" not in str(booking.enrichment_data)
        assert booking.enrichment_data["_guest_source_id"] == new_guest
        assert booking.enrichment_data["_guest_name"] == "Synthetic Other"
        assert "_change_key" not in booking.enrichment_data
        assert _calls(guesty_transport, new_guest) == 1

    async def test_reassigned_guest_lookup_failure_publishes_nothing(
        self, moved: _Moved, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A failed lookup never falls back to the previous guest."""
        await moved.store()
        guesty_transport.fail("guests_crud", 503, times=5)
        moved.rearrange(guest_id=moved.p.new_id("gst"))

        await moved.v3.sync()

        booking = (await moved.v3.bookings())[moved.booking_id]
        assert booking.status == "confirmed"
        assert booking.guest_phone_last4 is None
        assert "0100" not in str(booking.custom_data)
        assert booking.custom_data[DOOR_CODE] == "4321"

    async def test_no_guest_id_publishes_no_contact(
        self, moved: _Moved, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Without a guestId the contact is withheld; custom fields stay."""
        await moved.store()
        item = moved.rearrange()
        del item["guestId"]

        await moved.v3.sync()

        booking = (await moved.v3.bookings())[moved.booking_id]
        assert booking.status == "confirmed"
        assert booking.guest_phone_last4 is None
        assert "guest_phone" not in booking.custom_data
        assert "guest_phone_last4" not in booking.custom_data
        assert booking.custom_data[DOOR_CODE] == "4321"
        assert _calls(guesty_transport, moved.rid) == 0
        assert _calls(guesty_transport, moved.guest_id) == 0

    async def test_alteration_is_held(self, moved: _Moved) -> None:
        """A re-arranged stay under alteration keeps its last-good row."""
        stored = await moved.store()
        moved.rearrange(start=1, status="alteration")

        await moved.v3.sync()

        booking = (await moved.v3.bookings())[moved.booking_id]
        assert booking.status == "confirmed"
        assert booking.check_in_date == stored.check_in_date


class TestConfirmedAbsence:
    """Absence retires only with by-IDs confirmation (T033, FR-011a)."""

    async def test_cancelled_reservation_retires(self, moved: _Moved) -> None:
        """Cancelled and absent from the window: retired."""
        await moved.store()
        room = moved.v3.listing.pms_id
        moved.by_ids_only(
            [
                moved.p.by_ids_segment(
                    room, moved.local(2), moved.local(5), unit_id=room
                )
            ],
            status="canceled",
        )

        await moved.v3.sync()

        assert (await moved.v3.bookings())[moved.booking_id].status == "cancelled"

    async def test_omitted_with_control_returned_retires(self, moved: _Moved) -> None:
        """The control came back, so the omission is real absence."""
        await moved.store()

        await moved.v3.sync()

        bookings = await moved.v3.bookings()
        assert bookings[moved.booking_id].status == "cancelled"
        assert moved.v3.booking_id in bookings

    async def test_unverifiable_result_keeps_the_row(
        self, moved: _Moved, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A failed lookup is never absence."""
        await moved.store()
        guesty_transport.fail("v3_by_ids", 503)

        await moved.v3.sync()

        assert (await moved.v3.bookings())[moved.booking_id].status == "confirmed"

    async def test_steady_state_makes_no_by_ids_call(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Nothing absent, nothing asked."""
        await v3.setup()
        await v3.store()

        await v3.sync()
        await v3.sync()

        assert guesty_transport.counts["v3_by_ids"] == 0


class TestFaultedSearchRetiresNothing:
    """SC-003: a faulty search page never retires a booking (T035)."""

    @pytest.fixture
    async def two_chunks(
        self,
        v3: _Fixture,
        guesty_transport: GuestyFakeTransport,
        monkeypatch: pytest.MonkeyPatch,
    ) -> tuple[Listing, Listing]:
        """Two synced listings in separate chunks, five stays each, 2 per page."""
        monkeypatch.setattr(v3_search, "DEFAULT_PAGE_LIMIT", 2)
        monkeypatch.setattr(
            reservations_v3,
            "pack_chunks",
            lambda families: v3_search.pack_chunks(families, limit=1),
        )
        p = guesty_transport.payloads
        first, second = await v3.add_tracked(), await v3.add_tracked()
        guesty_transport.v3_items = [
            p.search_item(listing.pms_id, check_in=n + 1, check_out=n + 3)
            for listing in (first, second)
            for n in range(5)
        ]
        await self._cycle(v3, guesty_transport, first, second)
        return first, second

    @staticmethod
    async def _cycle(
        v3: _Fixture,
        transport: GuestyFakeTransport,
        *listings: Listing,
        cache: CalendarCache | None = None,
    ) -> list[Exception | None]:
        """Sync every listing through one cycle; return each one's error."""
        provider = transport.provider(reservations_api="v3")
        provider.begin_cycle([listing.pms_id for listing in listings])
        service = SyncService(
            v3.session,
            calendar_cache=cache or CalendarCache(ttl_seconds=0),
            session_factory=v3.factory,
        )
        errors: list[Exception | None] = []
        for listing in listings:
            try:
                await service.sync_listing(listing, provider)
                errors.append(None)
            except SyncServiceError as exc:
                errors.append(exc)
        return errors

    async def _assert_nothing_retired(
        self,
        v3: _Fixture,
        transport: GuestyFakeTransport,
        listings: tuple[Listing, Listing],
    ) -> None:
        """Both listings fail; rows and the cached feeds are unchanged."""
        before = {
            listing.id: {k: b.status for k, b in (await v3.bookings(listing)).items()}
            for listing in listings
        }
        assert all(
            set(statuses.values()) == {"confirmed"} and len(statuses) == 5
            for statuses in before.values()
        )
        cache = CalendarCache(ttl_seconds=3600)
        for listing in listings:
            cache.set(listing.ical_url_slug, f"feed-{listing.pms_id}")

        errors = await self._cycle(v3, transport, *listings, cache=cache)

        assert all(isinstance(error, SyncServiceError) for error in errors)
        for listing in listings:
            statuses = {k: b.status for k, b in (await v3.bookings(listing)).items()}
            assert statuses == before[listing.id]
            assert cache.get(listing.ical_url_slug) == f"feed-{listing.pms_id}"

    @pytest.mark.parametrize("chunk", [0, 1], ids=["first", "second"])
    @pytest.mark.parametrize("kind", SEARCH_PAGE_FAULTS)
    async def test_page_fault(
        self,
        v3: _Fixture,
        guesty_transport: GuestyFakeTransport,
        two_chunks: tuple[Listing, Listing],
        kind: str,
        chunk: int,
    ) -> None:
        """Any faulty page, in either chunk, retires nothing anywhere."""
        guesty_transport.fault_search_page(1, kind, listing_id=two_chunks[chunk].pms_id)

        await self._assert_nothing_retired(v3, guesty_transport, two_chunks)

    async def test_page_cap(
        self,
        v3: _Fixture,
        guesty_transport: GuestyFakeTransport,
        two_chunks: tuple[Listing, Listing],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A search still paging at the cap retires nothing."""
        monkeypatch.setattr(v3_search, "MAX_RESERVATION_PAGES", 2)

        await self._assert_nothing_retired(v3, guesty_transport, two_chunks)

    async def test_rate_limited(
        self,
        v3: _Fixture,
        guesty_transport: GuestyFakeTransport,
        two_chunks: tuple[Listing, Listing],
    ) -> None:
        """A page still rate-limited after the retries retires nothing."""
        guesty_transport.fail("v3_search", 429, times=4)

        await self._assert_nothing_retired(v3, guesty_transport, two_chunks)


def _events(feed: str) -> dict[str, dict[str, Any]]:
    """Index a rendered feed's events by UID."""
    from icalendar import Calendar

    events: dict[str, dict[str, Any]] = {}
    for event in Calendar.from_ical(feed).walk("VEVENT"):
        description = str(event.get("DESCRIPTION", ""))
        booking_line = [
            line for line in description.splitlines() if line.startswith("Booking ID:")
        ]
        events[str(event["UID"])] = {
            "DTSTART": event["DTSTART"].to_ical(),
            "DTEND": event["DTEND"].to_ical(),
            "SUMMARY": str(event["SUMMARY"]),
            "Booking ID": booking_line,
        }
    return events


class TestUpgradeContinuity:
    """Switching legacy → V3 republishes identical events (T022, SC-002)."""

    async def test_single_segment_feeds_are_identical(
        self,
        async_session: AsyncSession,
        async_session_factory: async_sessionmaker[AsyncSession],
        guesty_reference_account: Any,
    ) -> None:
        """UIDs, dates, summaries, booking IDs and door codes survive."""
        from src.services.calendar_service import CalendarService

        account = guesty_reference_account
        transport: GuestyFakeTransport = account.transport
        p = transport.payloads
        multi_segment = {
            str(item["reservationId"])
            for item in transport.v3_items
            if len(item["stay"]) > 1
        }
        # Give a few fresh stays door-code custom fields; legacy reads them
        # through the per-reservation endpoint, V3 inline.
        fresh = [
            item
            for item in transport.v3_items
            if str(item["reservationId"]) in account.freshness_ids
            and str(item["reservationId"]) not in multi_segment
        ]
        for n, item in enumerate(fresh[:4]):
            item["customFields"] = p.custom_fields({DOOR_CODE: f"{n:04d}"})

        listings: list[Listing] = []
        for n, pms_id in enumerate(account.tracked_listing_ids):
            listing = make_listing(pms_id=pms_id, slug=f"ref-{n}", timezone="UTC")
            async_session.add(listing)
            await async_session.commit()
            await async_session.refresh(listing)
            rooms = (
                account.sub_units if pms_id == account.multi_unit_parent else (pms_id,)
            )
            for m, room_id in enumerate(rooms):
                async_session.add(
                    make_room(
                        listing_id=listing.id,
                        pms_room_id=room_id,
                        slug=f"ref-{n}-{m}",
                    )
                )
            await async_session.commit()
            listings.append(listing)

        async def sync_and_render(
            provider: Any,
        ) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[Any, ...]]]:
            """Sync every listing, then return its feed events and codes."""
            service = SyncService(
                async_session,
                calendar_cache=CalendarCache(ttl_seconds=0),
                session_factory=async_session_factory,
            )
            events: dict[str, dict[str, Any]] = {}
            codes: dict[str, tuple[Any, ...]] = {}
            repo = BookingRepository(async_session)
            for listing in listings:
                await service.sync_listing(listing, provider)
                bookings = list(await repo.get_confirmed_for_listing(listing.id))
                for booking in bookings:
                    await async_session.refresh(booking)
                feed = CalendarService(
                    cache=CalendarCache(ttl_seconds=0)
                ).generate_ical(listing, bookings)
                events.update(_events(feed))
                for booking in bookings:
                    data = booking.custom_data or {}
                    codes[booking.pms_booking_id] = (
                        booking.guest_phone_last4,
                        data.get(DOOR_CODE),
                        data.get("confirmationCode"),
                    )
            return events, codes

        legacy_events, legacy_codes = await sync_and_render(
            transport.provider(reservations_api="legacy")
        )
        v3_start = len(transport.requests)
        provider = transport.provider(reservations_api="v3")
        provider.begin_cycle(list(account.tracked_listing_ids))
        v3_events, v3_codes = await sync_and_render(provider)

        def single_segment(events: dict[str, Any]) -> dict[str, Any]:
            """Drop the events of multi-segment stays (UIDs are hashed)."""
            return {
                uid: event
                for uid, event in events.items()
                if not any(
                    rid in line for line in event["Booking ID"] for rid in multi_segment
                )
            }

        expected = single_segment(legacy_events)
        assert len(expected) > 20
        assert single_segment(v3_events) == expected
        assert any(event["Booking ID"] for event in expected.values())

        def single_codes(codes: dict[str, Any]) -> dict[str, Any]:
            """Drop the bookings of multi-segment stays."""
            return {
                key: value
                for key, value in codes.items()
                if key.split("::")[0] not in multi_segment
            }

        assert single_codes(v3_codes) == single_codes(legacy_codes)
        assert {code for _, code, _ in legacy_codes.values()} >= {"0000", "0001"}
        assert {last4 for last4, _, _ in legacy_codes.values()} == {"0100"}

        retiring = {"reservations", "guests"}
        assert not [
            request
            for request in transport.requests[v3_start:]
            if request.resource in retiring
        ]

    async def test_description_fields_are_identical(self, v3: _Fixture) -> None:
        """Configured description lines survive the switch (T055, SC-009).

        Only the retired payment-total lines disappear; every other line,
        the guest count and derived nights included, renders unchanged.
        """
        from src.models.custom_field import CustomField
        from src.services.calendar_service import CalendarService

        await v3.setup(check_in=3, check_out=7, guests_count=3)
        p = v3.transport.payloads
        v3.transport.legacy_reservations = [p.as_legacy(v3.item)]
        retired = ("money_totalPaid", "money_balanceDue", "money_currency")
        names = ("source", "confirmationCode", "nightsCount", "guestsCount", "notes")
        enabled = [
            CustomField(
                listing_id=v3.listing.id,
                field_name=name,
                display_label=f"Label {n}",
                enabled=True,
                sort_order=n,
            )
            for n, name in enumerate((*names, *retired))
        ]
        v3.session.add_all(enabled)
        await v3.session.commit()

        async def description() -> list[str]:
            """Render the listing's single event's description lines."""
            from icalendar import Calendar

            repo = BookingRepository(v3.session)
            bookings = list(await repo.get_confirmed_for_listing(v3.listing.id))
            for booking in bookings:
                await v3.session.refresh(booking)
            feed = CalendarService(cache=CalendarCache(ttl_seconds=0)).generate_ical(
                v3.listing, bookings, custom_fields=enabled
            )
            (event,) = Calendar.from_ical(feed).walk("VEVENT")
            return str(event["DESCRIPTION"]).splitlines()

        await v3.sync_with(v3.transport.provider(reservations_api="legacy"), v3.listing)
        legacy = await description()
        await v3.sync()
        upgraded = await description()

        money = {f"Label {n}" for n in range(len(names), len(names) + len(retired))}
        assert [line for line in legacy if line.split(":")[0] in money]
        assert upgraded == [line for line in legacy if line.split(":")[0] not in money]
        assert [line for line in upgraded if line.startswith("Label")] == [
            "Label 0: direct",
            f"Label 1: {v3.item['confirmationCode']}",
            "Label 2: 4",
            "Label 3: 3",
            "Label 4: Synthetic other note; Synthetic guest note",
        ]
        assert upgraded[-1] == f"Booking ID: {v3.item['reservationId']}"

    async def test_custom_field_labels_are_unchanged(self, v3: _Fixture) -> None:
        """Account-defined labels resolve identically in V3 (T058, FR-035).

        Discovered labels come from the same account definitions, and a
        label the owner configured is never rewritten by the switch.
        """
        from sqlalchemy import delete, select
        from src.models.available_field import AvailableField
        from src.models.custom_field import CustomField
        from src.providers.guesty.service import clear_custom_field_definitions_cache

        p = v3.transport.payloads
        defined, undefined = p.new_id("cf"), p.new_id("cf")
        await v3.setup(
            custom_fields=p.custom_fields({defined: "4242", undefined: "Synthetic"})
        )
        v3.transport.legacy_reservations = [p.as_legacy(v3.item)]
        v3.transport.custom_field_definitions = [
            {"object": "reservation", "fieldId": defined, "key": "Door lock code"}
        ]
        v3.session.add(
            CustomField(
                listing_id=v3.listing.id,
                field_name=defined,
                display_label="Owner's label",
                enabled=True,
                sort_order=0,
            )
        )
        await v3.session.commit()

        async def labels() -> dict[str, Any]:
            """Return discovered and configured labels for both fields."""
            rows = await v3.session.execute(
                select(AvailableField.field_key, AvailableField.display_name).where(
                    AvailableField.listing_id == v3.listing.id,
                    AvailableField.field_key.in_((defined, undefined)),
                )
            )
            configured = await v3.session.execute(
                select(CustomField.field_name, CustomField.display_label).where(
                    CustomField.listing_id == v3.listing.id
                )
            )
            return {
                "discovered": dict(rows.all()),
                "configured": dict(configured.all()),
            }

        clear_custom_field_definitions_cache()
        await v3.sync_with(v3.transport.provider(reservations_api="legacy"), v3.listing)
        legacy = await labels()
        await v3.session.execute(
            delete(AvailableField).where(AvailableField.listing_id == v3.listing.id)
        )
        await v3.session.commit()
        clear_custom_field_definitions_cache()
        await v3.sync()
        upgraded = await labels()
        clear_custom_field_definitions_cache()

        assert legacy == {
            "discovered": {defined: "Door lock code", undefined: undefined},
            "configured": {defined: "Owner's label"},
        }
        assert upgraded == legacy


def _segment_span(segment: dict[str, Any]) -> tuple[datetime, datetime]:
    """Return a search segment's UTC ``(checkIn, checkOut)`` instants."""
    return (
        datetime.fromisoformat(segment["checkIn"]),
        datetime.fromisoformat(segment["checkOut"]),
    )


class TestMultiSegmentIntervals:
    """Each owned interval is its own booking in its own room (T040, R6)."""

    async def _property(self, v3: _Fixture) -> tuple[Listing, str, str]:
        """Create a tracked multi-unit listing with sub-units A and B."""
        p = v3.transport.payloads
        unit_a, unit_b = p.new_id("lst"), p.new_id("lst")
        listing = await v3.add_tracked(children=[unit_a, unit_b])
        return listing, unit_a, unit_b

    @staticmethod
    async def _room_feed(
        v3: _Fixture, listing: Listing, pms_room_id: str
    ) -> dict[str, dict[str, Any]]:
        """Render one room's feed and index its events by UID."""
        from src.services.calendar_service import CalendarService

        repo = BookingRepository(v3.session)
        rows = list(
            await repo.get_confirmed_for_listing(
                listing.id, room_id=v3.rooms[pms_room_id]
            )
        )
        for row in rows:
            await v3.session.refresh(row)
        return _events(
            CalendarService(cache=CalendarCache(ttl_seconds=0)).generate_ical(
                listing, rows
            )
        )

    @staticmethod
    def _spans(events: dict[str, dict[str, Any]]) -> list[tuple[bytes, bytes]]:
        """Return a feed's sorted ``(DTSTART, DTEND)`` pairs."""
        return sorted((e["DTSTART"], e["DTEND"]) for e in events.values())

    async def test_round_trip_books_each_interval_in_its_room(
        self, v3: _Fixture
    ) -> None:
        """A → B → A: three rows; each room feed holds only its dates."""
        listing, unit_a, unit_b = await self._property(v3)
        p = v3.transport.payloads
        item = p.round_trip_move(listing.pms_id, unit_a, unit_b)
        v3.transport.v3_items = [item]
        rid = item["reservationId"]

        await v3.sync(listing)

        rows = await v3.bookings(listing)
        first, middle, last = item["stay"]
        assert {
            key: (row.room_id, _as_utc(row.check_in_date), _as_utc(row.check_out_date))
            for key, row in rows.items()
        } == {
            f"{rid}::{unit_a}": (v3.rooms[unit_a], *_segment_span(first)),
            f"{rid}::{unit_b}": (v3.rooms[unit_b], *_segment_span(middle)),
            f"{rid}::{unit_a}#2": (v3.rooms[unit_a], *_segment_span(last)),
        }
        feed_a = await self._room_feed(v3, listing, unit_a)
        feed_b = await self._room_feed(v3, listing, unit_b)
        assert len(feed_a) == 2
        assert len(feed_b) == 1
        assert all(
            e["Booking ID"] == [f"Booking ID: {rid}"]
            for e in [*feed_a.values(), *feed_b.values()]
        )
        assert set(feed_a).isdisjoint(feed_b)
        # A's door is never scheduled while the guest is in B.
        (event_b,) = feed_b.values()
        assert all(
            e["DTEND"] <= event_b["DTSTART"] or e["DTSTART"] >= event_b["DTEND"]
            for e in feed_a.values()
        )

    async def test_move_from_another_tracked_listing(self, v3: _Fixture) -> None:
        """Each tracked listing books only its own segment (US3-3)."""
        p = v3.transport.payloads
        first = await v3.add_tracked()
        second = await v3.add_tracked()
        item = p.tracked_listing_move(first.pms_id, second.pms_id)
        v3.transport.v3_items = [item]
        rid = item["reservationId"]
        provider = v3.transport.provider(reservations_api="v3")
        provider.begin_cycle([first.pms_id, second.pms_id])

        (owned,) = await provider.get_reservations(second.pms_id)
        await v3.sync_with(provider, first)
        await v3.sync_with(provider, second)

        segment_x, segment_y = item["stay"]
        assert (owned.check_in, owned.check_out) == _segment_span(segment_y)
        for listing, segment in ((first, segment_x), (second, segment_y)):
            ((key, row),) = (await v3.bookings(listing)).items()
            assert key == f"{rid}::{listing.pms_id}"
            assert row.room_id == v3.rooms[listing.pms_id]
            assert (
                _as_utc(row.check_in_date),
                _as_utc(row.check_out_date),
            ) == _segment_span(segment)
        assert v3.transport.counts["v3_search"] == 1

    async def test_adjacent_same_room_segments_are_one_booking(
        self, v3: _Fixture
    ) -> None:
        """No ``#2`` row for a stay split into adjacent same-room segments."""
        listing, unit_a, _unit_b = await self._property(v3)
        p = v3.transport.payloads
        item = p.search_item(
            stay=[
                p.stay_segment(
                    unit_a,
                    p.check_in_at(3),
                    p.check_in_at(5),
                    parent_listing_id=listing.pms_id,
                ),
                p.stay_segment(
                    unit_a,
                    p.check_in_at(5),
                    p.check_out_at(8),
                    parent_listing_id=listing.pms_id,
                ),
            ]
        )
        v3.transport.v3_items = [item]
        rid = item["reservationId"]

        await v3.sync(listing)

        ((key, row),) = (await v3.bookings(listing)).items()
        assert key == f"{rid}::{unit_a}"
        assert (_as_utc(row.check_in_date), _as_utc(row.check_out_date)) == (
            p.check_in_at(3),
            p.check_out_at(8),
        )

    async def test_dropped_return_interval_retires_its_row(self, v3: _Fixture) -> None:
        """A → B → A becoming A → B retires ``A#2`` as a room change."""
        listing, unit_a, unit_b = await self._property(v3)
        p = v3.transport.payloads
        item = p.round_trip_move(listing.pms_id, unit_a, unit_b)
        v3.transport.v3_items = [item]
        rid = item["reservationId"]
        await v3.sync(listing)

        item["stay"] = item["stay"][:2]
        item["checkOut"] = item["stay"][-1]["checkOut"]
        item["checkOutDateLocalized"] = item["stay"][-1]["checkOutDateLocalized"]
        counts = await v3.sync(listing)

        assert {k: b.status for k, b in (await v3.bookings(listing)).items()} == {
            f"{rid}::{unit_a}": "confirmed",
            f"{rid}::{unit_b}": "confirmed",
            f"{rid}::{unit_a}#2": "cancelled",
        }
        assert counts["cancelled"] == 1
        assert v3.transport.counts["v3_by_ids"] == 0

    async def test_cancelled_round_trip_retires_every_interval(
        self, v3: _Fixture
    ) -> None:
        """A cancelled multi-interval stay leaves no door scheduled."""
        listing, unit_a, unit_b = await self._property(v3)
        p = v3.transport.payloads
        item = p.round_trip_move(listing.pms_id, unit_a, unit_b)
        v3.transport.v3_items = [item]
        await v3.sync(listing)

        item["status"] = "canceled"
        counts = await v3.sync(listing)

        rows = await v3.bookings(listing)
        assert len(rows) == 3
        assert {b.status for b in rows.values()} == {"cancelled"}
        assert counts["cancelled"] == 3

    async def test_unassigned_stay_is_listing_level(
        self, v3: _Fixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No room row, and the #116 informational log is kept (FR-019)."""
        listing, _unit_a, _unit_b = await self._property(v3)
        p = v3.transport.payloads
        item = p.unassigned_parent(listing.pms_id)
        v3.transport.v3_items = [item]
        rid = item["reservationId"]

        with caplog.at_level(logging.INFO, logger="src.services.sync_service"):
            await v3.sync(listing)

        ((key, row),) = (await v3.bookings(listing)).items()
        assert key == f"{rid}::{listing.pms_id}"
        assert row.room_id is None
        (record,) = [
            r for r in caplog.records if "not assigned to a specific unit" in r.message
        ]
        assert record.levelno == logging.INFO
        assert rid in record.message

    @pytest.mark.parametrize(
        ("booking_id", "expected"),
        [
            ("6650a", "6650a"),
            ("6650a::6640b", "6650a"),
            ("6650a::6640b#2", "6650a"),
            ("6650a::6640b#12", "6650a"),
        ],
    )
    def test_base_reservation_id(self, booking_id: str, expected: str) -> None:
        """``#n`` interval IDs map back to the reservation."""
        assert SyncService._extract_base_reservation_id(booking_id) == expected


def _reissue(v3: _Fixture, **changes: Any) -> dict[str, Any]:
    """Serve the first listing's stay again, re-built with *changes*."""
    item = v3.item
    changes.setdefault("guest_id", item["guestId"])
    changes.setdefault("check_in", 2)
    changes.setdefault("check_out", 5)
    reissued = v3.transport.payloads.search_item(
        None if "stay" in changes else v3.listing.pms_id,
        reservation_id=item["reservationId"],
        confirmation_code=item["confirmationCode"],
        **changes,
    )
    v3.transport.v3_items = [reissued]
    return reissued


def _shape(row: Booking) -> tuple[Any, ...]:
    """Return a stored row's export shape: status, dates and room."""
    return (
        row.status,
        _as_utc(row.check_in_date),
        _as_utc(row.check_out_date),
        row.room_id,
    )


class TestAlterationHold:
    """``alteration`` freezes the export shape (T044, FR-023, SC-008)."""

    async def test_live_stay_is_frozen_until_it_leaves_alteration(
        self, v3: _Fixture
    ) -> None:
        """confirmed → alteration (new dates) → confirmed."""
        p = v3.transport.payloads
        await v3.setup()
        await v3.sync()
        before = _shape((await v3.bookings())[v3.booking_id])
        feed_before = await v3.feed()

        _reissue(v3, status="alteration", check_in=3, check_out=9)
        counts = await v3.sync()

        rows = await v3.bookings()
        assert set(rows) == {v3.booking_id}
        assert _shape(rows[v3.booking_id]) == before
        assert counts["cancelled"] == 0
        assert await v3.feed() == feed_before

        _reissue(v3, status="confirmed", check_in=3, check_out=9)
        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert _shape(row) == (
            "confirmed",
            p.check_in_at(3),
            p.check_out_at(9),
            v3.room_id,
        )

    async def test_stay_derived_description_is_frozen(self, v3: _Fixture) -> None:
        """Nights and guests describe the published stay, not the altered one.

        Both derive from the in-flight stay, so they stay as stored for
        as long as the dates and room do (FR-023); notes keep refreshing.
        """
        await v3.setup(check_in=2, check_out=5, guests_count=2)
        await v3.sync()
        before = (await v3.bookings())[v3.booking_id].custom_data
        assert (before["nightsCount"], before["guestsCount"]) == ("3", "2")

        _reissue(
            v3,
            status="alteration",
            check_in=2,
            check_out=9,
            guests_count=6,
            notes={"other": "Synthetic altered note"},
        )
        await v3.sync()

        held = (await v3.bookings())[v3.booking_id].custom_data
        assert list(held) == list(before)
        assert (held["nightsCount"], held["guestsCount"]) == ("3", "2")
        assert held["notes"] == "Synthetic altered note"

        _reissue(v3, status="confirmed", check_in=2, check_out=9, guests_count=6)
        await v3.sync()

        released = (await v3.bookings())[v3.booking_id].custom_data
        assert (released["nightsCount"], released["guestsCount"]) == ("7", "6")

    async def test_enrichment_refreshes_during_the_hold(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Door code and guest contact stay current; the shape does not."""
        p = guesty_transport.payloads
        await v3.setup(custom_fields=p.custom_fields({DOOR_CODE: "1111"}))
        await v3.sync()
        before = _shape((await v3.bookings())[v3.booking_id])
        guest_id = v3.item["guestId"]
        guesty_transport.guests[guest_id] = p.guests_crud_body(
            guest_id, phone="+15555550199"
        )

        _reissue(
            v3,
            status="alteration",
            check_in=4,
            check_out=8,
            custom_fields=p.custom_fields({DOOR_CODE: "2222"}),
        )
        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert _shape(row) == before
        assert row.custom_data[DOOR_CODE] == "2222"
        assert row.enrichment_data[DOOR_CODE] == "2222"
        assert row.guest_phone_last4 == "0199"
        assert row.enrichment_data["guest_phone_last4"] == "0199"
        assert row.guest_name == "Synthetic Guest"

    async def test_reassigned_guest_never_leaks_during_the_hold(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A failed new-guest lookup publishes no previous-guest contact."""
        p = guesty_transport.payloads
        await v3.setup(custom_fields=p.custom_fields({DOOR_CODE: "1111"}))
        await v3.sync()
        assert (await v3.bookings())[v3.booking_id].guest_phone_last4 == "0100"
        guesty_transport.fail("guests_crud", 503, times=5)

        _reissue(
            v3,
            status="alteration",
            guest_id=p.new_id("gst"),
            custom_fields=p.custom_fields({DOOR_CODE: "1111"}),
        )
        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert row.status == "confirmed"
        assert row.guest_phone_last4 is None
        assert "0100" not in str(row.custom_data)
        assert "0100" not in str(row.enrichment_data)
        assert row.custom_data[DOOR_CODE] == "1111"

    async def test_failed_same_guest_lookup_keeps_the_name(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A transient lookup failure does not blank the published name."""
        p = guesty_transport.payloads
        await v3.setup(custom_fields=p.custom_fields({DOOR_CODE: "1111"}))
        await v3.sync()
        assert (await v3.bookings())[v3.booking_id].guest_name == "Synthetic Guest"
        guesty_transport.fail("guests_crud", 503, times=5)

        _reissue(
            v3,
            status="alteration",
            custom_fields=p.custom_fields({DOOR_CODE: "1111"}),
        )
        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert row.guest_name == "Synthetic Guest"
        assert row.guest_phone_last4 == "0100"

    async def test_every_interval_row_is_held(self, v3: _Fixture) -> None:
        """A → B → A under alteration (now B only) keeps all three rows."""
        p = v3.transport.payloads
        unit_a, unit_b = p.new_id("lst"), p.new_id("lst")
        listing = await v3.add_tracked(children=[unit_a, unit_b])
        item = p.round_trip_move(listing.pms_id, unit_a, unit_b)
        v3.transport.v3_items = [item]
        await v3.sync(listing)
        before = {k: _shape(r) for k, r in (await v3.bookings(listing)).items()}
        assert len(before) == 3

        item["status"] = "alteration"
        item["stay"] = [
            p.stay_segment(
                unit_b,
                p.check_in_at(20),
                p.check_out_at(25),
                parent_listing_id=listing.pms_id,
            )
        ]
        for key in ("checkIn", "checkInDateLocalized"):
            item[key] = item["stay"][0][key]
        for key in ("checkOut", "checkOutDateLocalized"):
            item[key] = item["stay"][-1][key]
        counts = await v3.sync(listing)

        after = {k: _shape(r) for k, r in (await v3.bookings(listing)).items()}
        assert after == before
        assert counts["cancelled"] == 0
        assert counts["inserted"] == 0
        assert v3.transport.counts["v3_by_ids"] == 0

    async def test_in_flight_move_out_of_listing_is_held(
        self, moved: _Moved, guesty_transport: GuestyFakeTransport
    ) -> None:
        """By-IDs ``alteration`` for a candidate is UNCONFIRMED: kept."""
        stored = await moved.store()
        room = moved.v3.listing.pms_id
        moved.by_ids_only(
            [
                moved.p.by_ids_segment(
                    moved.untracked,
                    moved.local(2),
                    moved.local(5),
                    unit_id=moved.untracked,
                )
            ],
            status="alteration",
        )
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([room])
        await provider.get_reservations(room)

        (result,) = (await provider.confirm_absent(room, [moved.rid])).values()
        await moved.v3.sync_with(provider, moved.v3.listing)

        assert result.verdict is AbsenceVerdict.UNCONFIRMED
        row = (await moved.v3.bookings())[moved.booking_id]
        assert _shape(row) == _shape(stored)

    async def test_first_seen_alteration_is_withheld_quietly(
        self,
        v3: _Fixture,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Not stored, DEBUG only, not in the panel; stored once confirmed."""
        await v3.setup(status="alteration")
        rid = v3.item["reservationId"]

        with caplog.at_level(logging.DEBUG, logger="src.services.sync_service"):
            counts = await v3.sync()

        assert await v3.bookings() == {}
        assert counts == {"inserted": 0, "updated": 0, "cancelled": 0}
        records = [r for r in caplog.records if rid in r.getMessage()]
        assert records
        assert {r.levelno for r in records} == {logging.DEBUG}
        assert await _withheld(v3) == []
        # Nothing is stored, so nothing is enriched.
        assert guesty_transport.counts["guests_crud"] == 0
        assert guesty_transport.counts["custom_fields"] == 0

        _reissue(v3, status="confirmed")
        await v3.sync()

        assert (await v3.bookings())[v3.booking_id].status == "confirmed"

    async def test_cancelled_rows_stay_untouched(self, v3: _Fixture) -> None:
        """A held reservation never revives or re-dates a retired row."""
        await v3.setup()
        await v3.sync()
        _reissue(v3, status="canceled")
        await v3.sync()
        retired = (await v3.bookings())[v3.booking_id]
        stamp = retired.updated_at

        _reissue(v3, status="alteration", check_in=4, check_out=9)
        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert row.status == "cancelled"
        assert row.updated_at == stamp


async def _withheld(v3: _Fixture) -> list[dict[str, Any]]:
    """Return the admin withheld-bookings panel's rows."""
    from httpx import ASGITransport, AsyncClient
    from src.database import get_db
    from src.main import create_app

    app = create_app()

    async def override_get_db() -> Any:
        async with v3.factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/bookings/withheld")
    assert response.status_code == 200
    return response.json()["bookings"]


class TestUnknownStatus:
    """An unknown V3 status is withheld, never retired (T045, US4-3/4)."""

    async def test_withheld_and_listed_in_the_panel(
        self, v3: _Fixture, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Stored verbatim, out of the feed, in the panel, warned."""
        await v3.setup()
        await v3.sync()
        assert len(await v3.feed()) == 1

        _reissue(v3, status="synthetic_new")
        with caplog.at_level(logging.WARNING):
            counts = await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert row.status == "synthetic_new"
        assert counts["cancelled"] == 0
        assert await v3.feed() == {}
        (entry,) = await _withheld(v3)
        assert entry["pms_booking_id"] == v3.booking_id
        assert entry["status"] == "synthetic_new"
        assert any(
            "synthetic_new" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    async def test_majority_unclassified_keeps_last_good(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Over half of four or more unknown: abort, nothing changes."""
        p = guesty_transport.payloads
        await v3.setup()
        pms_id = v3.listing.pms_id
        others = [
            p.search_item(pms_id, check_in=6 + 3 * n, check_out=8 + 3 * n)
            for n in range(3)
        ]
        guesty_transport.v3_items = [v3.item, *others]
        await v3.sync()
        before = {k: _shape(r) for k, r in (await v3.bookings()).items()}
        feed_before = await v3.feed()
        assert len(before) == 4

        for item in others:
            item["status"] = "synthetic_new"
        with pytest.raises(SyncServiceError, match="unrecognised"):
            await v3.sync()

        assert {k: _shape(r) for k, r in (await v3.bookings()).items()} == before
        assert await v3.feed() == feed_before
        await v3.session.refresh(v3.listing)
        assert v3.listing.last_sync_error


BOOKKEEPING = ("_guest_source_id", "_guest_name", "_change_key")

# A stay wholly outside the freshness window (starts > 30 days ahead).
FAR = {"check_in": 45, "check_out": 48}


class TestEnrichmentReuse:
    """Change-key reuse of stored enrichment (T049, research R9)."""

    @staticmethod
    async def _steady(v3: _Fixture, **item: Any) -> None:
        """Sync a far stay with an inline door code once, then reset counts."""
        p = v3.transport.payloads
        item.setdefault("custom_fields", p.custom_fields({DOOR_CODE: "4242"}))
        await v3.setup(**{**FAR, **item})
        await v3.sync()
        v3.transport.counts.clear()

    async def test_unchanged_far_stay_reuses_everything(self, v3: _Fixture) -> None:
        """No guest or custom-field call; name, contact and code are kept."""
        await self._steady(v3)
        stored = (await v3.bookings())[v3.booking_id]
        assert stored.enrichment_data["_change_key"]
        assert stored.enrichment_data["_guest_name"] == "Synthetic Guest"
        assert stored.enrichment_data["_guest_source_id"] == v3.item["guestId"]

        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert v3.transport.counts["guests_crud"] == 0
        assert v3.transport.counts["custom_fields"] == 0
        assert row.guest_name == "Synthetic Guest"
        assert row.guest_phone_last4 == "0100"
        assert row.custom_data[DOOR_CODE] == "4242"
        assert (
            row.enrichment_data["_change_key"] == stored.enrichment_data["_change_key"]
        )

    async def test_far_stay_without_guest_reuses(self, v3: _Fixture) -> None:
        """A reservation with no guest needs no guest provenance."""
        await self._steady(v3)
        _reissue(v3, **FAR, custom_fields=v3.item["customFields"])
        del v3.transport.v3_items[0]["guestId"]
        await v3.sync()
        v3.transport.counts.clear()

        await v3.sync()

        assert v3.transport.counts["guests_crud"] == 0
        assert v3.transport.counts["custom_fields"] == 0

    @pytest.mark.parametrize(
        "change",
        ["guest", "custom_field", "notes", "dates"],
    )
    async def test_any_change_refetches(self, v3: _Fixture, change: str) -> None:
        """A changed change key forces a fresh guest lookup."""
        p = v3.transport.payloads
        await self._steady(v3)
        guest_id = p.new_id("gst") if change == "guest" else v3.item["guestId"]
        v3.transport.guests[guest_id] = p.guests_crud_body(
            guest_id, phone="+15555550177"
        )
        changes: dict[str, Any] = {
            **FAR,
            "guest_id": guest_id,
            "custom_fields": v3.item["customFields"],
        }
        if change == "custom_field":
            changes["custom_fields"] = p.custom_fields({DOOR_CODE: "5151"})
        elif change == "notes":
            changes["notes"] = {"other": "Changed synthetic note"}
        elif change == "dates":
            changes.update(check_in=46, check_out=49)
        _reissue(v3, **changes)

        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert v3.transport.counts["guests_crud"] == 1
        assert row.guest_phone_last4 == "0177"
        expected = "5151" if change == "custom_field" else "4242"
        assert row.custom_data[DOOR_CODE] == expected

    async def test_null_enrichment_is_never_reused(self, v3: _Fixture) -> None:
        """A row with no stored enrichment is enriched, however far away."""
        await v3.setup(**FAR)
        await v3.store(check_in=45, check_out=48, enrichment_data=None)

        await v3.sync()

        assert v3.transport.counts["guests_crud"] == 1

    async def test_missing_guest_name_is_not_reused(self, v3: _Fixture) -> None:
        """Stored contact without a recorded name forces a lookup."""
        await self._steady(v3)
        row = (await v3.bookings())[v3.booking_id]
        data = dict(row.enrichment_data)
        del data["_guest_name"]
        row.enrichment_data = data
        await v3.session.commit()

        await v3.sync()

        assert v3.transport.counts["guests_crud"] == 1

    async def test_stay_in_the_freshness_window_is_never_reused(
        self, v3: _Fixture
    ) -> None:
        """Every sync re-reads a near stay (US5-3, FR-030)."""
        p = v3.transport.payloads
        await self._steady(v3, check_in=20, check_out=23)

        for _ in range(2):
            await v3.sync()

        assert v3.transport.counts["guests_crud"] == 2
        guest_id = v3.item["guestId"]
        v3.transport.guests[guest_id] = p.guests_crud_body(
            guest_id, phone="+15555550188"
        )
        await v3.sync()
        assert (await v3.bookings())[v3.booking_id].guest_phone_last4 == "0188"

    async def test_held_stay_is_judged_on_its_stored_dates(self, v3: _Fixture) -> None:
        """Frozen near dates keep refreshing while in-flight dates are far."""
        p = v3.transport.payloads
        await self._steady(v3, check_in=3, check_out=6)
        held = {
            "status": "alteration",
            "check_in": 60,
            "check_out": 63,
            "custom_fields": v3.item["customFields"],
        }
        _reissue(v3, **held)
        await v3.sync()
        v3.transport.counts.clear()
        guest_id = v3.item["guestId"]
        v3.transport.guests[guest_id] = p.guests_crud_body(
            guest_id, phone="+15555550166"
        )

        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert row.status == "confirmed"
        assert _as_utc(row.check_in_date) == p.check_in_at(3)
        assert v3.transport.counts["guests_crud"] == 1
        assert row.guest_phone_last4 == "0166"

    @pytest.mark.parametrize("failure", ["guest", "custom_field"])
    async def test_failed_lookup_persists_no_change_key(
        self, v3: _Fixture, failure: str
    ) -> None:
        """The next sync retries a failed enrichment."""
        await v3.setup(**FAR)
        await v3.store(
            check_in=45,
            check_out=48,
            custom_data={DOOR_CODE: "1234"},
            enrichment_data={DOOR_CODE: "1234"},
        )
        v3.transport.custom_fields[v3.item["reservationId"]] = [
            {"fieldId": DOOR_CODE, "value": "1234"}
        ]
        resource = "guests_crud" if failure == "guest" else "custom_fields"
        v3.transport.fail(resource, 503)

        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert "_change_key" not in row.enrichment_data
        assert row.custom_data[DOOR_CODE] == "1234"
        v3.transport.counts.clear()

        await v3.sync()

        assert v3.transport.counts["guests_crud"] == 1
        row = (await v3.bookings())[v3.booking_id]
        assert row.enrichment_data["_change_key"]

    async def test_bookkeeping_keys_never_reach_the_export(self, v3: _Fixture) -> None:
        """Neither custom data, discovered fields nor descriptions carry them."""
        from src.models.custom_field import CustomField
        from src.repositories.available_field_repository import (
            AvailableFieldRepository,
        )
        from src.services.calendar_service import CalendarService

        await self._steady(v3, check_in=2, check_out=5)
        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert set(BOOKKEEPING) <= set(row.enrichment_data)
        assert not set(BOOKKEEPING) & set(row.custom_data)
        fields = await AvailableFieldRepository(v3.session).get_for_listing(
            v3.listing.id
        )
        assert not set(BOOKKEEPING) & {f.field_key for f in fields}
        enabled = [
            CustomField(
                listing_id=v3.listing.id,
                field_name=key,
                display_label=key,
                enabled=True,
                sort_order=n,
            )
            for n, key in enumerate((*BOOKKEEPING, DOOR_CODE))
        ]
        feed = CalendarService(cache=CalendarCache(ttl_seconds=0)).generate_ical(
            v3.listing, [row], custom_fields=enabled
        )
        assert "4242" in feed
        for key in BOOKKEEPING:
            assert key not in feed
        assert row.enrichment_data["_change_key"] not in feed


class TestGuestLookupFailure:
    """A failed guest lookup outside a hold (T050, FR-031, FR-032)."""

    @staticmethod
    async def _stored(
        v3: _Fixture, *, reassigned: bool, status: int = 503, **extra: Any
    ) -> None:
        """Store last-good contact and fail the next lookup with ``status``.

        The contact belongs to the stay's guest, or to another guest when
        ``reassigned``.
        """
        await v3.setup()
        p = v3.transport.payloads
        contact = {
            "guest_phone": "+15555550100",
            "guest_phone_last4": "0100",
            "_guest_source_id": p.new_id("gst") if reassigned else v3.item["guestId"],
            **extra,
        }
        await v3.store(
            custom_data={"guest_phone_last4": "0100"},
            enrichment_data=contact,
            guest_phone_last4="0100",
        )
        v3.transport.fail("guests_crud", status)

    @pytest.mark.parametrize("status", [503, 404])
    async def test_reassignment_publishes_no_previous_contact(
        self, v3: _Fixture, status: int
    ) -> None:
        """The previous guest's last-4 and name are never published."""
        await self._stored(
            v3, reassigned=True, status=status, _guest_name="Stored Name"
        )

        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert row.status == "confirmed"
        assert row.guest_phone_last4 is None
        assert row.guest_name is None
        assert "0100" not in str(row.custom_data)
        assert "0100" not in str(row.enrichment_data)
        assert "Stored Name" not in str(row.enrichment_data)

    @pytest.mark.parametrize("status", [503, 404])
    async def test_same_guest_keeps_contact_and_recorded_name(
        self, v3: _Fixture, status: int
    ) -> None:
        """Last-good contact and ``_guest_name`` survive (US5-4)."""
        await self._stored(
            v3, reassigned=False, status=status, _guest_name="Stored Name"
        )
        guest_id = v3.item["guestId"]

        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert row.guest_phone_last4 == "0100"
        assert row.guest_name == "Stored Name"
        assert row.enrichment_data["_guest_name"] == "Stored Name"
        assert row.enrichment_data["_guest_source_id"] == guest_id
        assert "_change_key" not in row.enrichment_data

    async def test_previous_release_row_keeps_its_guest_name(
        self, v3: _Fixture
    ) -> None:
        """Without ``_guest_name`` a matching source keeps the stored name."""
        await self._stored(v3, reassigned=False)

        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert row.guest_name == "Synthetic Guest"
        assert row.guest_phone_last4 == "0100"

    async def test_previous_release_row_without_source_uses_placeholder(
        self, v3: _Fixture
    ) -> None:
        """No matching source: the stored name is not trusted (US5-5)."""
        await self._stored(v3, reassigned=True)

        await v3.sync()

        row = (await v3.bookings())[v3.booking_id]
        assert row.guest_name is None
        assert row.guest_phone_last4 is None
