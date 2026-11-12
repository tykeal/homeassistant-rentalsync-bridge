# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Contract tests for the Guesty Reservations V3 resources (spec 004).

Pins the requests the V3 reservation source sends to
``/v1/reservations-v3/search`` (contracts/guesty-v3-search.md) and
``/v1/reservations-v3`` by IDs (contracts/guesty-v3-by-ids.md).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from src.providers.base import (
    AbsenceResult,
    AbsenceVerdict,
    PMSAuthenticationError,
    PMSGuest,
    PMSProviderError,
)
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


class TestGuestsCrudRequest:
    """``get_guest`` reads ``/v1/guests-crud/{id}`` in V3 mode (FR-001)."""

    async def test_v3_reads_guests_crud_with_fields(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """The path and projection are pinned; the body maps to PMSGuest."""
        guest_id = guesty_transport.payloads.new_id("gst")
        guesty_transport.guests[guest_id] = guesty_transport.payloads.guests_crud_body(
            guest_id
        )
        provider = guesty_transport.provider(reservations_api="v3")

        guest = await provider.get_guest(guest_id)

        assert guest == PMSGuest(
            guest_id=guest_id,
            full_name="Synthetic Guest",
            phone="+15555550100",
            email="guest@example.com",
        )
        (request,) = guesty_transport.requests
        assert request.path == f"/v1/guests-crud/{guest_id}"
        assert request.query == {"fields": ["fullName phone email"]}
        assert guesty_transport.counts["guests"] == 0

    async def test_v3_missing_guest_is_none(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A 404 is a legitimately absent guest, not an error."""
        provider = guesty_transport.provider(reservations_api="v3")

        assert await provider.get_guest("6630missing") is None
        assert guesty_transport.counts["guests_crud"] == 1

    async def test_v3_server_error_raises(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A 5xx surfaces as PMSProviderError so the sync keeps last-good."""
        guesty_transport.fail("guests_crud", 503)
        provider = guesty_transport.provider(reservations_api="v3")

        with pytest.raises(PMSProviderError):
            await provider.get_guest("6630anyguest")
        assert guesty_transport.counts["guests_crud"] == 1

    async def test_v3_unauthorised_raises_authentication_error(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A 401 surfaces as PMSAuthenticationError."""
        guesty_transport.fail("guests_crud", 401)
        provider = guesty_transport.provider(reservations_api="v3")

        with pytest.raises(PMSAuthenticationError):
            await provider.get_guest("6630anyguest")
        assert guesty_transport.counts["guests_crud"] == 1

    async def test_legacy_still_reads_v1_guests(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Legacy mode keeps the previous release's guest resource."""
        guest_id = guesty_transport.payloads.new_id("gst")
        guesty_transport.guests[guest_id] = guesty_transport.payloads.guests_crud_body(
            guest_id
        )
        provider = guesty_transport.provider(reservations_api="legacy")

        guest = await provider.get_guest(guest_id)

        assert guest is not None
        assert guest.full_name == "Synthetic Guest"
        (request,) = guesty_transport.requests
        assert request.path == f"/v1/guests/{guest_id}"
        assert request.query == {}
        assert guesty_transport.counts["guests_crud"] == 0


class _Account:
    """One tracked listing with a live (control) stay, plus an untracked one."""

    def __init__(self, transport: GuestyFakeTransport) -> None:
        """Register the listings and the control reservation."""
        self.transport = transport
        self.p = transport.payloads
        self.listing = self.p.new_id("lst")
        self.untracked = self.p.new_id("lst")
        transport.add_listing(self.listing)
        transport.add_listing(self.untracked)
        self.control = self.p.search_item(self.listing, check_in=20, check_out=22)
        transport.v3_items = [self.control]

    def local(self, offset: int) -> str:
        """Return the local date *offset* days from today."""
        return self.p.day(offset).date().isoformat()

    def absent(
        self,
        rooms: list[str],
        *,
        status: str = "confirmed",
        start: int = 5,
        nights: int = 2,
        guest_id: str | None = None,
    ) -> str:
        """Add a by-IDs-only reservation over consecutive *rooms*; return its ID."""
        rid = self.p.new_id("res")
        stay = [
            self.p.by_ids_segment(
                room,
                self.local(start + n * nights),
                self.local(start + (n + 1) * nights),
                unit_id=room,
            )
            for n, room in enumerate(rooms)
        ]
        self.transport.by_ids_overrides[rid] = self.p.by_ids_item(
            rid, stay, status=status, guest_id=guest_id
        )
        return rid

    async def confirm(
        self,
        candidates: list[str],
        *,
        after_search: Callable[[], None] | None = None,
    ) -> dict[str, AbsenceResult]:
        """Search the listing, then confirm *candidates* absent."""
        provider = self.transport.provider(reservations_api="v3")
        provider.begin_cycle([self.listing])
        await provider.get_reservations(self.listing)
        if after_search is not None:
            after_search()
        return await provider.confirm_absent(self.listing, candidates)


def _verdicts(results: dict[str, AbsenceResult]) -> dict[str, AbsenceVerdict]:
    """Return just the verdict per candidate."""
    return {rid: result.verdict for rid, result in results.items()}


def _levels(caplog: pytest.LogCaptureFixture) -> set[str]:
    """Return the WARNING-or-worse levels logged."""
    return {r.levelname for r in caplog.records if r.levelno >= logging.WARNING}


class TestV3ByIdsRequest:
    """The exact by-IDs query and batching (research R5)."""

    async def test_query_is_form_exploded_candidates_plus_control(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Sorted, de-duplicated candidates then the control; nothing else."""
        account = _Account(guesty_transport)
        second = account.absent([account.listing])
        first = account.absent([account.listing], status="canceled")

        await account.confirm([second, first, second])

        (request,) = guesty_transport.requests_for("v3_by_ids")
        assert request.path == "/v1/reservations-v3"
        assert request.query == {
            "reservationIds": [
                *sorted([first, second]),
                account.control["reservationId"],
            ]
        }

    async def test_at_most_nine_candidates_and_the_control_per_call(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Twenty candidates take three calls, each ending with the control."""
        account = _Account(guesty_transport)
        candidates = [
            account.absent([account.listing], status="canceled") for _ in range(20)
        ]

        results = await account.confirm(candidates)

        calls = guesty_transport.requests_for("v3_by_ids")
        assert [len(c.query["reservationIds"]) for c in calls] == [10, 10, 3]
        control = account.control["reservationId"]
        assert all(c.query["reservationIds"][-1] == control for c in calls)
        assert [rid for c in calls for rid in c.query["reservationIds"][:-1]] == (
            sorted(candidates)
        )
        assert set(_verdicts(results).values()) == {AbsenceVerdict.CONFIRMED_ABSENT}

    async def test_no_candidates_no_request(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """An empty candidate list costs nothing."""
        account = _Account(guesty_transport)

        assert await account.confirm([]) == {}
        assert guesty_transport.counts["v3_by_ids"] == 0

    async def test_no_control_available(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """An empty search still asks; returned verdicts stand, omitted wait."""
        account = _Account(guesty_transport)
        guesty_transport.v3_items = []
        cancelled = account.absent([account.listing], status="cancelled")
        omitted = account.p.new_id("res")

        results = await account.confirm([cancelled, omitted])

        (request,) = guesty_transport.requests_for("v3_by_ids")
        assert request.query == {"reservationIds": sorted([cancelled, omitted])}
        assert _verdicts(results) == {
            cancelled: AbsenceVerdict.CONFIRMED_ABSENT,
            omitted: AbsenceVerdict.UNCONFIRMED,
        }


class TestV3ByIdsOutcomes:
    """Every outcome row of contracts/guesty-v3-by-ids.md."""

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("canceled", AbsenceVerdict.CONFIRMED_ABSENT),
            ("cancelled", AbsenceVerdict.CONFIRMED_ABSENT),
            ("inquiry", AbsenceVerdict.CONFIRMED_ABSENT),
            ("reserved", AbsenceVerdict.CONFIRMED_ABSENT),
            ("alteration", AbsenceVerdict.UNCONFIRMED),
        ],
    )
    async def test_returned_status(
        self,
        guesty_transport: GuestyFakeTransport,
        status: str,
        expected: AbsenceVerdict,
    ) -> None:
        """Cancelled and excluded statuses retire; ``alteration`` is held."""
        account = _Account(guesty_transport)
        rid = account.absent([account.untracked, account.listing], status=status)

        assert _verdicts(await account.confirm([rid])) == {rid: expected}

    async def test_no_segment_in_scope_is_absent(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A stay moved wholly out of the listing retires."""
        account = _Account(guesty_transport)
        rid = account.absent([account.untracked])

        assert _verdicts(await account.confirm([rid])) == {
            rid: AbsenceVerdict.CONFIRMED_ABSENT
        }

    @pytest.mark.parametrize("start", [-200, 400], ids=["before", "after"])
    async def test_outside_the_window_is_absent(
        self, guesty_transport: GuestyFakeTransport, start: int
    ) -> None:
        """A stay moved wholly outside the padded search window retires."""
        account = _Account(guesty_transport)
        rid = account.absent([account.untracked, account.listing], start=start)

        assert _verdicts(await account.confirm([rid])) == {
            rid: AbsenceVerdict.CONFIRMED_ABSENT
        }

    async def test_first_segment_untracked_is_reattributed(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Re-attribution carries the normalised reservation; no abort."""
        account = _Account(guesty_transport)
        rid = account.absent([account.untracked, account.listing])

        result = (await account.confirm([rid]))[rid]

        assert result.verdict is AbsenceVerdict.REATTRIBUTED
        assert result.reservation is not None
        assert result.reservation.pms_booking_id == rid
        (interval,) = result.reservation.intervals
        assert interval.room_id == account.listing
        assert interval.check_in is None
        assert interval.check_out is None
        assert interval.local_dates == (account.local(7), account.local(9))

    @pytest.mark.parametrize("unit", ["unitId", "unitTypeId"])
    async def test_first_segment_searched_is_inconsistent(
        self, guesty_transport: GuestyFakeTransport, unit: str
    ) -> None:
        """A live stay the search should have returned aborts only this listing."""
        account = _Account(guesty_transport)
        rid = account.absent([account.listing, account.untracked])
        first = guesty_transport.by_ids_overrides[rid]["stay"][0]
        first["unitId" if unit == "unitTypeId" else "unitTypeId"] = account.untracked
        if unit == "unitTypeId":
            first.pop("unitId")
        provider = guesty_transport.provider(reservations_api="v3")
        provider.begin_cycle([account.listing])
        await provider.get_reservations(account.listing)

        results = await provider.confirm_absent(account.listing, [rid])

        assert _verdicts(results) == {rid: AbsenceVerdict.INCONSISTENT}
        # The cached search is not poisoned: no re-search, no error.
        await provider.get_reservations(account.listing)
        assert guesty_transport.counts["v3_search"] == 1

    @pytest.mark.parametrize("gap", [0, 1], ids=["utc_adjacent", "same_day_gap"])
    async def test_ambiguous_same_room_boundary_is_unconfirmed(
        self,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
        gap: int,
    ) -> None:
        """Same-room segments meeting on one local date cannot be resolved."""
        account = _Account(guesty_transport)
        p = account.p
        second_in = p.check_in_at(7) if gap else p.check_out_at(7)
        item = p.search_item(
            stay=[
                p.stay_segment(account.untracked, p.check_in_at(4), p.check_out_at(5)),
                p.stay_segment(account.listing, p.check_out_at(5), p.check_out_at(7)),
                p.stay_segment(account.listing, second_in, p.check_out_at(9)),
            ]
        )
        rid = item["reservationId"]
        guesty_transport.by_ids_overrides[rid] = p.as_by_ids(item)

        with caplog.at_level(logging.WARNING):
            results = await account.confirm([rid])

        assert _verdicts(results) == {rid: AbsenceVerdict.UNCONFIRMED}
        assert "WARNING" in _levels(caplog)

    async def test_omitted_with_control_returned_is_absent(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """The control proves the parameter worked, so omission is absence."""
        account = _Account(guesty_transport)
        omitted = account.p.new_id("res")

        assert _verdicts(await account.confirm([omitted])) == {
            omitted: AbsenceVerdict.CONFIRMED_ABSENT
        }

    async def test_control_missing_is_unconfirmed_with_error(
        self,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Without the control back, an omission proves nothing."""
        account = _Account(guesty_transport)
        omitted = account.p.new_id("res")

        with caplog.at_level(logging.WARNING):
            results = await account.confirm(
                [omitted], after_search=guesty_transport.v3_items.clear
            )

        assert _verdicts(results) == {omitted: AbsenceVerdict.UNCONFIRMED}
        assert "ERROR" in _levels(caplog)

    @pytest.mark.parametrize("fault", ["unrequested", "duplicate"])
    async def test_unrequested_or_duplicate_id_voids_the_batch(
        self,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        fault: str,
    ) -> None:
        """A response not limited to the requested IDs proves nothing."""
        account = _Account(guesty_transport)
        cancelled = account.absent([account.listing], status="canceled")
        stranger = account.absent([account.untracked], status="canceled")
        serve = guesty_transport._serve_v3_by_ids

        def corrupt(*args: Any) -> Any:
            """Add an unrequested item, or repeat a requested one."""
            response = serve(*args)
            body = response.json()
            view = guesty_transport.by_ids_view()
            body.append(view[stranger] if fault == "unrequested" else body[0])
            return httpx.Response(200, json=body)

        monkeypatch.setattr(guesty_transport, "_serve_v3_by_ids", corrupt)

        with caplog.at_level(logging.WARNING):
            results = await account.confirm([cancelled])

        assert _verdicts(results) == {cancelled: AbsenceVerdict.UNCONFIRMED}
        assert "ERROR" in _levels(caplog)

    async def test_ignored_parameter_retires_nothing(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """Unrelated account items, the control included, but not the candidate."""
        account = _Account(guesty_transport)
        unrelated = account.p.search_item(account.untracked)
        guesty_transport.v3_items.append(unrelated)
        still_there = account.absent([account.listing])
        guesty_transport.ignore_reservation_ids = True
        guesty_transport.ignored_ids_limit = 2

        results = await account.confirm([still_there])

        assert _verdicts(results) == {still_there: AbsenceVerdict.UNCONFIRMED}

    @pytest.mark.parametrize(
        "body",
        [{"results": []}, [{"status": "canceled"}], [{"_id": "x", "stay": []}]],
        ids=["not_a_list", "no_id", "empty_stay"],
    )
    async def test_malformed_response_voids_the_batch(
        self,
        guesty_transport: GuestyFakeTransport,
        monkeypatch: pytest.MonkeyPatch,
        body: Any,
    ) -> None:
        """An unusable response leaves every candidate unconfirmed."""
        account = _Account(guesty_transport)
        cancelled = account.absent([account.listing], status="canceled")
        monkeypatch.setattr(
            guesty_transport,
            "_serve_v3_by_ids",
            lambda *_args: httpx.Response(200, json=body),
        )

        assert _verdicts(await account.confirm([cancelled])) == {
            cancelled: AbsenceVerdict.UNCONFIRMED
        }

    async def test_segment_without_unit_voids_the_batch(
        self, guesty_transport: GuestyFakeTransport
    ) -> None:
        """A segment with neither ``unitId`` nor ``unitTypeId`` is invalid."""
        account = _Account(guesty_transport)
        cancelled = account.absent([account.listing], status="canceled")
        segment = guesty_transport.by_ids_overrides[cancelled]["stay"][0]
        del segment["unitId"], segment["unitTypeId"]

        assert _verdicts(await account.confirm([cancelled])) == {
            cancelled: AbsenceVerdict.UNCONFIRMED
        }

    @pytest.mark.parametrize(("status", "level"), [(400, "ERROR"), (503, "WARNING")])
    async def test_http_error_is_unconfirmed(
        self,
        guesty_transport: GuestyFakeTransport,
        caplog: pytest.LogCaptureFixture,
        status: int,
        level: str,
    ) -> None:
        """A failed lookup never means absent; a 400 is logged as an error."""
        account = _Account(guesty_transport)
        cancelled = account.absent([account.listing], status="canceled")
        guesty_transport.fail("v3_by_ids", status, body={"message": "Bad ids"})

        with caplog.at_level(logging.WARNING):
            results = await account.confirm([cancelled])

        assert _verdicts(results) == {cancelled: AbsenceVerdict.UNCONFIRMED}
        assert level in _levels(caplog)
