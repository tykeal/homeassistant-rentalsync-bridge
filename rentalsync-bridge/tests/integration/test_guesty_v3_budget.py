# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Request-budget tests for the Guesty V3 source (spec 004 US5, T051)."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from src.models.booking import Booking
from src.models.listing import Listing
from src.providers.guesty.service import (
    GuestyProvider,
    clear_custom_field_definitions_cache,
)
from src.services.calendar_service import CalendarCache
from src.services.sync_service import SyncService

from tests.conftest import (
    GuestyFakeTransport,
    GuestyReferenceAccount,
    make_listing,
    make_room,
)

DOOR_CODE = "door_code"
PAGE_SIZE = 100

# Fake-transport resource -> request-count log resource (FR-043).
LOGGED_AS = {
    "reservations": "search",
    "v3_search": "search",
    "v3_by_ids": "by_ids",
    "guests": "guests",
    "guests_crud": "guests",
    "custom_fields": "custom_fields",
    "listings": "listings",
    "listing": "listings",
}


class _Budget:
    """The reference account tracked in a database, synced cycle by cycle."""

    def __init__(
        self,
        session: AsyncSession,
        factory: async_sessionmaker[AsyncSession],
        account: GuestyReferenceAccount,
    ) -> None:
        """Hold the collaborators every cycle shares."""
        self.session = session
        self.factory = factory
        self.account = account
        self.transport: GuestyFakeTransport = account.transport
        self.listings: list[Listing] = []
        self.last_requests: list[Any] = []

    async def track(self) -> None:
        """Create the tracked listings and their rooms."""
        account = self.account
        for n, pms_id in enumerate(account.tracked_listing_ids):
            listing = make_listing(pms_id=pms_id, slug=f"budget-{n}", timezone="UTC")
            self.session.add(listing)
            await self.session.commit()
            await self.session.refresh(listing)
            rooms = (
                account.sub_units if pms_id == account.multi_unit_parent else (pms_id,)
            )
            for m, room_id in enumerate(rooms):
                self.session.add(
                    make_room(
                        listing_id=listing.id,
                        pms_room_id=room_id,
                        slug=f"budget-{n}-{m}",
                    )
                )
            await self.session.commit()
            self.listings.append(listing)

    async def cycle(self, mode: str) -> Counter[str]:
        """Run one scheduler-shaped sync cycle; return its request counts."""
        before = Counter(self.transport.counts)
        start = len(self.transport.requests)
        # A real token manager knows its account, so custom-field labels
        # are cached across cycles as in production.
        token_manager = AsyncMock()
        token_manager.get_token = AsyncMock(return_value="synthetic-token")
        token_manager.account_key = "synthetic-account"
        provider = GuestyProvider(
            token_manager=token_manager,
            http_client=self.transport.client(),
            reservations_api=mode,
        )
        provider.begin_cycle([listing.pms_id for listing in self.listings])
        service = SyncService(
            self.session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=self.factory,
        )
        for listing in self.listings:
            await service.sync_listing(listing, provider)
        await provider.aclose()
        self.last_requests = self.transport.requests[start:]
        counts = Counter(self.transport.counts)
        counts.subtract(before)
        return +counts

    async def cycles(self, mode: str, n: int = 2) -> list[Counter[str]]:
        """Run ``n`` cycles from an empty database in one mode."""
        await self.session.execute(delete(Booking))
        await self.session.commit()
        clear_custom_field_definitions_cache()
        return [await self.cycle(mode) for _ in range(n)]


@pytest.fixture
async def budget(
    async_session: AsyncSession,
    async_session_factory: async_sessionmaker[AsyncSession],
    guesty_reference_account: GuestyReferenceAccount,
) -> _Budget:
    """The reference account, tracked, with inline custom fields everywhere.

    Every tracked reservation carries ``customFields`` (a door code on the
    fresh ones), and every legacy item a ``lastUpdatedAt`` so the previous
    release can reuse its stored enrichment: the comparison is fair.
    """
    transport = guesty_reference_account.transport
    p = transport.payloads
    fresh = set(guesty_reference_account.freshness_ids)
    for n, item in enumerate(transport.v3_items):
        code = {DOOR_CODE: f"{n:04d}"} if item["reservationId"] in fresh else {}
        item["customFields"] = p.custom_fields(code)
    for item in transport.legacy_reservations:
        item["lastUpdatedAt"] = "2026-01-01T00:00:00.000Z"
    clear_custom_field_definitions_cache()
    state = _Budget(async_session, async_session_factory, guesty_reference_account)
    await state.track()
    return state


def _total(counts: Counter[str]) -> int:
    """Return the number of requests a cycle made."""
    return sum(counts.values())


class TestRequestBudget:
    """SC-006 and SC-007 on the reference account."""

    async def test_v3_never_exceeds_the_previous_release(self, budget: _Budget) -> None:
        """Each V3 cycle costs no more than the legacy cycle (SC-006)."""
        legacy = await budget.cycles("legacy")
        v3 = await budget.cycles("v3")

        for old, new in zip(legacy, v3, strict=True):
            assert _total(new) <= _total(old), (dict(old), dict(new))
        assert _total(v3[1]) < _total(legacy[1])

    async def test_steady_state_refreshes_only_the_freshness_window(
        self, budget: _Budget
    ) -> None:
        """Cycle two looks up only freshness-window guests; no custom fields."""
        transport = budget.transport
        fresh = set(budget.account.freshness_ids)
        guests = {
            item["guestId"]
            for item in transport.v3_items
            if item["reservationId"] in fresh
        }

        first, second = await budget.cycles("v3")

        assert first["custom_fields"] == 0
        assert second["custom_fields"] == 0
        looked_up = {
            r.path.rsplit("/", 1)[-1]
            for r in budget.last_requests
            if r.resource == "guests_crud"
        }
        assert looked_up == guests
        assert second["guests_crud"] == len(guests)
        assert set(second) <= {"listing", "v3_search", "guests_crud"}

    async def test_pages_follow_the_window_not_the_history(
        self, budget: _Budget
    ) -> None:
        """One search sequence of ceil(in_window/100) pages (SC-007)."""
        transport = budget.transport
        p = transport.payloads
        expected = math.ceil(len(budget.account.in_window_ids) / PAGE_SIZE)

        (before,) = await budget.cycles("v3", n=1)
        tracked = budget.account.tracked_listing_ids
        transport.v3_items.extend(
            p.search_item(tracked[n % 2], check_in=-2000 + n * 5, check_out=-1998)
            for n in range(300)
        )
        (after,) = await budget.cycles("v3", n=1)

        assert before["v3_search"] == after["v3_search"] == expected
        searches = [r for r in budget.last_requests if r.resource == "v3_search"]
        assert [r.query.get("skip", ["0"])[0] for r in searches] == [
            str(n * PAGE_SIZE) for n in range(expected)
        ]
        assert len({tuple(r.query["filter[listingId][in]"]) for r in searches}) == 1

    async def test_untracked_reservations_are_never_fetched(
        self, budget: _Budget
    ) -> None:
        """The search names only tracked listings; by-IDs no foreign stays."""
        account = budget.account
        untracked = set(account.untracked_ids)

        await budget.cycles("v3")

        for request in budget.transport.requests:
            if request.resource == "v3_search":
                (ids,) = request.query["filter[listingId][in]"]
                assert account.untracked_listing_id not in ids.split(",")
            elif request.resource == "v3_by_ids":
                named = {
                    part
                    for values in request.query.values()
                    for value in values
                    for part in value.split(",")
                }
                assert not named & untracked
            elif request.resource == "custom_fields":
                assert request.path.split("/")[-2] not in untracked
        foreign_guests = {
            item["guestId"]
            for item in budget.transport.v3_items
            if item["reservationId"] in untracked
        }
        assert (
            not {
                r.path.rsplit("/", 1)[-1]
                for r in budget.transport.requests
                if r.resource == "guests_crud"
            }
            & foreign_guests
        )

    @pytest.mark.parametrize("mode", ["legacy", "v3"])
    async def test_a_cycle_logs_its_request_counts(
        self, budget: _Budget, caplog: pytest.LogCaptureFixture, mode: str
    ) -> None:
        """One INFO line per cycle reports the requests by resource (FR-043)."""
        caplog.set_level("INFO", logger="src.providers.guesty.service")

        cycles = await budget.cycles(mode)

        lines = [
            r.getMessage()
            for r in caplog.records
            if r.getMessage().startswith("Guesty requests this cycle:")
        ]
        expected = []
        for counts in cycles:
            logged: Counter[str] = Counter()
            for resource, n in counts.items():
                logged[LOGGED_AS.get(resource, "other")] += n
            names = ("search", "by_ids", "guests", "custom_fields", "listings")
            expected.append(
                "Guesty requests this cycle: "
                + " ".join(f"{name}={logged[name]}" for name in (*names, "other"))
            )
        assert lines == expected
