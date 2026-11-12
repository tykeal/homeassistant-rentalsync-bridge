# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the Guesty Reservations V3 resources (spec 004).

Pins the requests the V3 reservation source sends to
``/v1/reservations-v3/search`` (contracts/guesty-v3-search.md).
"""

from datetime import UTC, datetime

import pytest
from src.providers.guesty import reservations_v3

from tests.conftest import GuestyFakeTransport, GuestyV3Payloads

# Late in the UTC day, so a naive local-date computation would differ.
FROZEN_NOW = datetime(2026, 11, 12, 23, 30, tzinfo=UTC)

# Research R3: UTC date(now - 91 d) - 1 d and UTC date(now + 365 d) + 1 d.
EXPECTED_CHECKOUT_GTE = "2026-08-12"
EXPECTED_CHECKIN_LTE = "2027-11-13"


class TestV3SearchRequest:
    """The exact search query (FR-013, FR-016, FR-040)."""

    @pytest.fixture
    def frozen(self, monkeypatch: pytest.MonkeyPatch) -> GuestyFakeTransport:
        """A fake account whose dates and window use a frozen clock."""
        monkeypatch.setattr(reservations_v3, "_utcnow", lambda: FROZEN_NOW)
        return GuestyFakeTransport(GuestyV3Payloads(now=FROZEN_NOW))

    async def test_first_and_second_page_query(
        self, frozen: GuestyFakeTransport
    ) -> None:
        """Pages 0 and 1 carry the exact documented parameters, no more."""
        p = frozen.payloads
        single = p.new_id("lst")
        parent = p.new_id("lst")
        units = [p.new_id("lst"), p.new_id("lst")]
        untracked = p.new_id("lst")
        frozen.add_listing(single)
        frozen.add_listing(parent, children=units)
        frozen.add_listing(untracked)
        frozen.v3_items = [
            p.search_item(single, check_in=start, check_out=start + 2)
            for start in range(-60, 300, 3)
        ] + [p.search_item(untracked, check_in=5, check_out=7)]

        provider = frozen.provider(reservations_api="v3")
        provider.begin_cycle([single, parent])
        await provider.get_reservations(single)

        searches = frozen.requests_for("v3_search")
        assert len(searches) == 2
        expected_ids = ",".join(sorted([single, parent, *units]))
        for page, request in enumerate(searches):
            assert request.path == "/v1/reservations-v3/search"
            assert request.query == {
                "filter[listingId][in]": [expected_ids],
                "filter[checkOut][gte]": [EXPECTED_CHECKOUT_GTE],
                "filter[checkIn][lte]": [EXPECTED_CHECKIN_LTE],
                "sort": ["_id"],
                "limit": ["100"],
                "skip": [str(page * 100)],
            }
            assert "fields" not in request.query
            assert untracked not in request.query["filter[listingId][in]"][0]
        # V3 mode never touches the retiring legacy list.
        assert frozen.counts["reservations"] == 0

    async def test_imported_child_contributes_parent_unit_type(
        self, frozen: GuestyFakeTransport
    ) -> None:
        """A tracked child's fetch scope adds its parent unit type."""
        p = frozen.payloads
        parent = p.new_id("lst")
        child = p.new_id("lst")
        frozen.add_listing(parent, children=[child])
        frozen.add_listing(child, parent_id=parent)

        provider = frozen.provider(reservations_api="v3")
        provider.begin_cycle([child])
        await provider.get_reservations(child)

        (search,) = frozen.requests_for("v3_search")
        assert search.query["filter[listingId][in]"] == [
            ",".join(sorted([parent, child]))
        ]
