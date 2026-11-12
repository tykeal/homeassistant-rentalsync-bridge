# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for syncing through the Guesty V3 source (spec 004 US1)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from src.models.booking import Booking
from src.models.listing import Listing
from src.repositories.booking_repository import BookingRepository
from src.services.calendar_service import CalendarCache
from src.services.sync_service import SyncService, SyncServiceError

from tests.conftest import (
    GuestyFakeTransport,
    make_booking,
    make_listing,
    make_room,
)

DOOR_CODE = "door_code"


class _Fixture:
    """One tracked single-unit listing, its room and one stay."""

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
        self.item: dict[str, Any]

    async def setup(self, *, custom_fields: Any = None, **item: Any) -> None:
        """Create the listing, its room and the account's single stay."""
        p = self.transport.payloads
        pms_id = p.new_id("lst")
        self.transport.add_listing(pms_id)
        self.listing = make_listing(
            pms_id=pms_id, slug=f"v3-{pms_id[-6:]}", timezone="UTC"
        )
        self.session.add(self.listing)
        await self.session.commit()
        await self.session.refresh(self.listing)
        room = make_room(
            listing_id=self.listing.id, pms_room_id=pms_id, slug=f"r-{pms_id[-6:]}"
        )
        self.session.add(room)
        await self.session.commit()
        await self.session.refresh(room)
        self.room_id = room.id
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
    ) -> Booking:
        """Store a previously synced booking for the stay."""
        p = self.transport.payloads
        booking = make_booking(
            listing_id=self.listing.id,
            room_id=self.room_id,
            pms_booking_id=pms_booking_id or self.booking_id,
            guest_name="Synthetic Guest",
            check_in_date=p.check_in_at(2),
            check_out_date=p.check_out_at(5),
            custom_data=custom_data,
        )
        booking.enrichment_data = enrichment_data
        self.session.add(booking)
        await self.session.commit()
        await self.session.refresh(booking)
        return booking

    async def sync(self) -> dict[str, int]:
        """Run one V3 sync of the listing."""
        provider = self.transport.provider(reservations_api="v3")
        provider.begin_cycle([self.listing.pms_id])
        service = SyncService(
            self.session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=self.factory,
        )
        return await service.sync_listing(self.listing, provider)

    async def bookings(self) -> dict[str, Booking]:
        """Return the listing's stored bookings by booking ID, refreshed."""
        rows = await BookingRepository(self.session).get_for_listing(self.listing.id)
        for row in rows:
            await self.session.refresh(row)
        return {row.pms_booking_id: row for row in rows}


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


class TestInterimAbsence:
    """Absent reservations are never retired before US2's by-ID check."""

    async def test_absent_reservation_is_kept(
        self, v3: _Fixture, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A stored stay missing from the search stays published."""
        await v3.setup()
        missing = guesty_transport.payloads.new_id("res")
        await v3.store(pms_booking_id=f"{missing}::{v3.listing.pms_id}")

        await v3.sync()

        bookings = await v3.bookings()
        assert bookings[f"{missing}::{v3.listing.pms_id}"].status == "confirmed"
        assert bookings[v3.booking_id].status == "confirmed"


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
