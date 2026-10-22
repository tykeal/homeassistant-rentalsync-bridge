# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for GuestyProvider."""

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from src.providers.base import (
    PMSAuthenticationError,
    PMSConnectionError,
    PMSGuest,
    PMSListing,
    PMSProvider,
    PMSProviderError,
    PMSRateLimitError,
    PMSReservation,
    PMSRoom,
)
from src.providers.guesty import service
from src.providers.guesty.auth import GuestyTokenManager
from src.providers.guesty.service import (
    DEFAULT_LOOKBACK_DAYS,
    GUESTY_BASE_URL,
    RESERVATION_FIELDS,
    GuestyProvider,
    _extract_custom_data,
    _format_address,
    _parse_date,
    _parse_retry_after,
    clear_custom_field_definitions_cache,
)

# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------


def _rel(days: int, suffix: str = "") -> str:
    """Build a reservation date string relative to now.

    Sample stays must stay inside the provider's default 365-day window,
    which is now applied client-side, so fixed calendar dates would rot.

    Args:
        days: Offset in days from now.
        suffix: Optional time suffix appended to the date.

    Returns:
        ISO date string, optionally with *suffix* appended.
    """
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%d") + suffix


SAMPLE_LISTINGS_PAGE1 = {
    "results": [
        {
            "_id": "L1",
            "title": "Beach House",
            "timezone": "America/New_York",
            "address": {"full": "123 Beach Rd, Miami, FL"},
        },
        {
            "_id": "L2",
            "nickname": "Mountain Lodge",
            "timezone": "US/Mountain",
            "address": None,
        },
    ]
}

SAMPLE_LISTINGS_PAGE2 = {
    "results": [
        {
            "_id": "L3",
            "title": "City Apt",
            "timezone": "UTC",
            "address": {"street": "456 Main St", "city": "NYC"},
        },
    ]
}

SAMPLE_RESERVATIONS = {
    "results": [
        {
            "_id": "R1",
            "status": "confirmed",
            "guest": {"_id": "G1", "fullName": "Alice Smith"},
            "checkIn": _rel(-10, "T14:00:00Z"),
            "checkOut": _rel(-6, "T11:00:00Z"),
            "listingId": "L1",
            "source": "airbnb",
            "confirmationCode": "ABC123",
            "nightsCount": 4,
            "guestsCount": 2,
            "money": {
                "totalPaid": 500,
                "balanceDue": 0,
                "currency": "USD",
            },
        },
        {
            "_id": "R2",
            "status": "checked_in",
            "guest": {"_id": "G2", "fullName": "Bob Jones"},
            "checkIn": _rel(-2),
            "checkOut": _rel(3),
            "listingId": "L1",
        },
        {
            "_id": "R3",
            "status": "inquiry",
            "guest": {"_id": "G3", "fullName": "Excluded"},
            "checkIn": _rel(20),
            "checkOut": _rel(24),
            "listingId": "L1",
        },
        {
            "_id": "R4",
            "status": "canceled",
            "guest": {"_id": "G4", "fullName": "Cancelled Guest"},
            "checkIn": _rel(40),
            "checkOut": _rel(44),
            "listingId": "L1",
        },
    ]
}

SAMPLE_LISTING_MULTI_UNIT = {
    "_id": "L1",
    "title": "Beach House",
    "childListings": [
        {"_id": "C1", "title": "Suite A", "roomType": "suite"},
        {"_id": "C2", "nickname": "Room B", "roomType": "standard"},
    ],
}

SAMPLE_LISTING_SINGLE_UNIT = {
    "_id": "L1",
    "title": "Beach House",
    "propertyType": "apartment",
    "childListings": [],
}

SAMPLE_GUEST = {
    "_id": "G1",
    "fullName": "Alice Smith",
    "phone": "+15551234567",
    "email": "alice@example.com",
}

SAMPLE_CUSTOM_FIELDS = [
    {"fieldId": "cf_1", "value": "early_checkin"},
    {"fieldId": "cf_2", "value": "ground_floor"},
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_token_manager():
    """Create a mock GuestyTokenManager with test defaults."""
    mgr = AsyncMock(spec=GuestyTokenManager)
    mgr.get_token = AsyncMock(return_value="test-token")
    mgr.invalidate_cache = AsyncMock()
    mgr.cached_expires_at = datetime.now(UTC) + timedelta(hours=24)
    return mgr


@pytest.fixture
def mock_http_client():
    """Create a mock httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def guesty_provider(mock_token_manager, mock_http_client):
    """Create a GuestyProvider with mocked dependencies."""
    return GuestyProvider(
        token_manager=mock_token_manager,
        http_client=mock_http_client,
    )


def _make_response(json_data, status_code=200, headers=None):
    """Build a mock httpx.Response from JSON data."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        headers=headers or {},
        request=httpx.Request("GET", f"{GUESTY_BASE_URL}/test"),
    )


# ---------------------------------------------------------------------------
# Basic provider contract
# ---------------------------------------------------------------------------


class TestProviderType:
    """Tests for GuestyProvider type and identity."""

    def test_is_pms_provider(self, guesty_provider):
        """Test that GuestyProvider is a PMSProvider."""
        assert isinstance(guesty_provider, PMSProvider)

    def test_provider_type_is_guesty(self, guesty_provider):
        """Test that provider_type returns guesty."""
        assert guesty_provider.provider_type == "guesty"

    def test_has_separate_custom_fields(self, guesty_provider):
        """Test Guesty reports separate custom fields."""
        assert guesty_provider.has_separate_custom_fields is True


# ---------------------------------------------------------------------------
# get_listings
# ---------------------------------------------------------------------------


class TestGetListings:
    """Tests for GuestyProvider.get_listings."""

    @pytest.mark.asyncio
    async def test_maps_listings(self, guesty_provider, mock_http_client):
        """Test that API listings map to PMSListing DTOs."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_LISTINGS_PAGE1)
        )

        listings = await guesty_provider.get_listings()

        assert len(listings) == 2
        assert all(isinstance(item, PMSListing) for item in listings)
        assert listings[0].pms_id == "L1"
        assert listings[0].name == "Beach House"
        assert listings[0].timezone == "America/New_York"
        assert listings[0].address == "123 Beach Rd, Miami, FL"
        assert listings[1].pms_id == "L2"
        assert listings[1].name == "Mountain Lodge"

    @pytest.mark.asyncio
    async def test_pagination(self, guesty_provider, mock_http_client):
        """Test that multiple pages are fetched."""
        full_page = {
            "results": [
                {"_id": f"L{i}", "title": f"P{i}", "timezone": "UTC"}
                for i in range(100)
            ],
        }

        mock_http_client.request = AsyncMock(
            side_effect=[
                _make_response(full_page),
                _make_response(SAMPLE_LISTINGS_PAGE2),
            ]
        )

        listings = await guesty_provider.get_listings()

        assert len(listings) == 101
        assert mock_http_client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_results(self, guesty_provider, mock_http_client):
        """Test that empty results return an empty list."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response({"results": []})
        )

        listings = await guesty_provider.get_listings()
        assert listings == []

    @pytest.mark.asyncio
    async def test_skips_listings_without_id(self, guesty_provider, mock_http_client):
        """Test that listings without _id are skipped."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(
                {
                    "results": [
                        {"_id": "", "title": "No ID"},
                        {"_id": "L1", "title": "Valid"},
                    ]
                }
            )
        )

        listings = await guesty_provider.get_listings()
        assert len(listings) == 1
        assert listings[0].pms_id == "L1"


# ---------------------------------------------------------------------------
# get_reservations
# ---------------------------------------------------------------------------


class TestGetReservations:
    """Tests for GuestyProvider.get_reservations."""

    @pytest.mark.asyncio
    async def test_maps_reservations(self, guesty_provider, mock_http_client):
        """Test that reservations map to PMSReservation DTOs."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")

        assert all(isinstance(r, PMSReservation) for r in reservations)
        confirmed = [r for r in reservations if r.pms_booking_id == "R1"]
        assert len(confirmed) == 1
        assert confirmed[0].status == "confirmed"
        assert confirmed[0].guest_name == "Alice Smith"
        assert confirmed[0].guest_id == "G1"

    @pytest.mark.asyncio
    async def test_custom_data_populated(self, guesty_provider, mock_http_client):
        """Test that custom_data is populated from reservation fields."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")
        r1 = next(r for r in reservations if r.pms_booking_id == "R1")

        assert r1.custom_data["source"] == "airbnb"
        assert r1.custom_data["confirmationCode"] == "ABC123"
        assert r1.custom_data["nightsCount"] == 4
        assert r1.custom_data["guestsCount"] == 2
        assert r1.custom_data["money_totalPaid"] == 500
        assert r1.custom_data["money_balanceDue"] == 0
        assert r1.custom_data["money_currency"] == "USD"

    @pytest.mark.asyncio
    async def test_empty_custom_data_when_no_extra_fields(
        self, guesty_provider, mock_http_client
    ):
        """Test custom_data is empty when reservation has no extra fields."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")
        r2 = next(r for r in reservations if r.pms_booking_id == "R2")

        assert r2.custom_data == {}

    @pytest.mark.asyncio
    async def test_status_mapping(self, guesty_provider, mock_http_client):
        """Test that Guesty statuses map to normalised statuses."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")
        statuses = {r.pms_booking_id: r.status for r in reservations}

        assert statuses["R1"] == "confirmed"
        assert statuses["R2"] == "checked_in"
        assert statuses["R4"] == "cancelled"

    def test_cancelled_two_l_spelling_mapped(self):
        """Test the two-"l" cancelled spelling maps to cancelled."""
        result = GuestyProvider._map_reservation(
            {
                "_id": "R9",
                "status": "cancelled",
                "checkIn": _rel(40),
                "checkOut": _rel(44),
            },
            "L1",
        )

        assert result is not None
        assert result.status == "cancelled"

    def test_unrecognised_status_kept_and_warned(self, caplog):
        """Test an unknown status is carried through with a warning."""
        with caplog.at_level(logging.WARNING, logger="src.providers.guesty.service"):
            result = GuestyProvider._map_reservation(
                {
                    "_id": "R9",
                    "status": "Canceled_By_Channel",
                    "checkIn": _rel(40),
                    "checkOut": _rel(44),
                },
                "L1",
            )

        # Must NOT be None: dropping it would let the prune loop retire it.
        assert result is not None
        assert result.status == "canceled_by_channel"
        assert "'canceled_by_channel'" in caplog.text
        assert "R9" in caplog.text

    @pytest.mark.asyncio
    async def test_excludes_inquiry_and_reserved(
        self,
        guesty_provider,
        mock_http_client,
    ):
        """Test that inquiry statuses are excluded."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")
        booking_ids = [r.pms_booking_id for r in reservations]

        assert "R3" not in booking_ids

    @pytest.mark.asyncio
    async def test_assigned_sub_unit_wins_over_listing_id(
        self, guesty_provider, mock_http_client
    ):
        """Test the assigned sub-unit is used as the room reference.

        A multi-unit reservation carries the parent unit-type listing in
        ``listingId`` while the assigned child unit lives on the stay
        segment, so the room reference must come from the stay segment.
        """
        mock_http_client.request = AsyncMock(
            return_value=_make_response(
                {
                    "results": [
                        {
                            "_id": "R10",
                            "status": "checked_in",
                            "checkIn": _rel(-1),
                            "checkOut": _rel(2),
                            "listingId": "L1",
                            "stay": [{"unitTypeId": "L1", "unitId": "C2"}],
                        }
                    ]
                }
            )
        )

        reservations = await guesty_provider.get_reservations("L1")

        assert reservations[0].room_ids == ("C2",)

    @pytest.mark.asyncio
    async def test_legacy_mtl_assignment_used(self, guesty_provider, mock_http_client):
        """Test the legacy ``mtl`` block supplies the assigned sub-unit."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(
                {
                    "results": [
                        {
                            "_id": "R11",
                            "status": "confirmed",
                            "checkIn": _rel(-1),
                            "checkOut": _rel(2),
                            "listingId": "L1",
                            "mtl": {"assigned": True, "_id": "C1"},
                        }
                    ]
                }
            )
        )

        reservations = await guesty_provider.get_reservations("L1")

        assert reservations[0].room_ids == ("C1",)

    @pytest.mark.asyncio
    async def test_unassigned_multi_unit_falls_back_to_parent(
        self, guesty_provider, mock_http_client
    ):
        """Test an unassigned multi-unit stay keeps the parent reference."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(
                {
                    "results": [
                        {
                            "_id": "R12",
                            "status": "confirmed",
                            "checkIn": _rel(-1),
                            "checkOut": _rel(2),
                            "listingId": "L1",
                            "mtl": {"assigned": False},
                            "stay": [{"unitTypeId": "L1"}],
                        }
                    ]
                }
            )
        )

        reservations = await guesty_provider.get_reservations("L1")

        assert reservations[0].room_ids == ("L1",)

    @pytest.mark.asyncio
    async def test_single_unit_room_reference_unchanged(
        self, guesty_provider, mock_http_client
    ):
        """Test a single-unit reservation still maps to the listing ID."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_RESERVATIONS)
        )

        reservations = await guesty_provider.get_reservations("L1")
        r1 = next(r for r in reservations if r.pms_booking_id == "R1")

        assert r1.room_ids == ("L1",)

    @pytest.mark.asyncio
    async def test_scopes_query_with_documented_filters(
        self, guesty_provider, mock_http_client
    ):
        """Test the listing scope is sent as a documented ``filters`` array.

        Guesty's v1 ``/reservations`` endpoint exposes only ``viewId``,
        ``filters``, ``fields``, ``sort``, ``limit`` and ``skip`` -- there is
        no top-level ``listingId`` or ``status`` parameter, and no
        ``checkIn``/``checkOut`` stay-window parameters.  Unknown parameters
        are silently ignored, so sending them scoped nothing at all.
        https://open-api-docs.guesty.com/reference/reservationsV2List
        """
        mock_http_client.request = AsyncMock(
            return_value=_make_response({"results": [], "count": 0})
        )

        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 12, 31, tzinfo=UTC)
        await guesty_provider.get_reservations("L1", start_date=start, end_date=end)

        call_kwargs = mock_http_client.request.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert json.loads(params["filters"]) == [
            {"field": "listingId", "operator": "$in", "value": ["L1"]}
        ]
        for unsupported in (
            "listingId",
            "status",
            "checkIn",
            "checkOut",
            "startDate",
            "endDate",
        ):
            assert unsupported not in params

    @pytest.mark.asyncio
    async def test_window_filter_applied_client_side(
        self, guesty_provider, mock_http_client
    ):
        """Test stays are filtered to the requested window after fetching."""
        payload = {
            "count": 3,
            "results": [
                {
                    "_id": "BEFORE",
                    "status": "confirmed",
                    "checkIn": _rel(-40),
                    "checkOut": _rel(-35),
                    "listingId": "L1",
                },
                {
                    "_id": "IN-PROGRESS",
                    "status": "confirmed",
                    "checkIn": _rel(-2),
                    "checkOut": _rel(3),
                    "listingId": "L1",
                },
                {
                    "_id": "AFTER",
                    "status": "confirmed",
                    "checkIn": _rel(40),
                    "checkOut": _rel(45),
                    "listingId": "L1",
                },
            ],
        }
        mock_http_client.request = AsyncMock(return_value=_make_response(payload))

        now = datetime.now(UTC)
        reservations = await guesty_provider.get_reservations(
            "L1",
            start_date=now - timedelta(days=5),
            end_date=now + timedelta(days=5),
        )

        # A stay straddling "now" must survive: that is the issue #99 defect.
        assert {r.pms_booking_id for r in reservations} == {"IN-PROGRESS"}

    @pytest.mark.asyncio
    async def test_default_window_includes_in_progress_stays(
        self, guesty_provider, mock_http_client
    ):
        """Test the default window reaches a year into the past."""
        payload = {
            "count": 2,
            "results": [
                {
                    "_id": "OLD-BUT-RUNNING",
                    "status": "confirmed",
                    "checkIn": _rel(-(DEFAULT_LOOKBACK_DAYS - 2)),
                    "checkOut": _rel(2),
                    "listingId": "L1",
                },
                {
                    "_id": "ANCIENT",
                    "status": "confirmed",
                    "checkIn": _rel(-(DEFAULT_LOOKBACK_DAYS + 20)),
                    "checkOut": _rel(-(DEFAULT_LOOKBACK_DAYS + 10)),
                    "listingId": "L1",
                },
            ],
        }
        mock_http_client.request = AsyncMock(return_value=_make_response(payload))

        reservations = await guesty_provider.get_reservations("L1")

        ids = {r.pms_booking_id for r in reservations}
        assert DEFAULT_LOOKBACK_DAYS == 365
        assert "OLD-BUT-RUNNING" in ids
        assert "ANCIENT" not in ids


def _scoped_provider(listing_json, reservations_json):
    """Build a provider serving a listing document and one reservation page.

    Args:
        listing_json: Body returned for ``GET /v1/listings/{id}``.
        reservations_json: Body returned for ``GET /v1/reservations``.

    Returns:
        Tuple of (GuestyProvider, list collecting reservation request URLs).
    """
    seen = []

    def handler(request):
        """Serve the listing document or the reservation page.

        Args:
            request: Outgoing request.

        Returns:
            Mocked JSON response.
        """
        if request.url.path.startswith("/v1/listings/"):
            return httpx.Response(200, json=listing_json)
        seen.append(request.url)
        return httpx.Response(200, json=reservations_json)

    token_manager = AsyncMock()
    token_manager.get_token = AsyncMock(return_value="tok")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=GUESTY_BASE_URL,
    )
    return GuestyProvider(token_manager=token_manager, http_client=client), seen


class TestReservationListingScope:
    """Reservations must never leak between listings (issue #111).

    Guesty's v1 ``/reservations`` endpoint has no top-level ``listingId``
    parameter and silently ignores unknown query parameters, so an account's
    entire reservation set was previously stored under every listing.
    """

    @staticmethod
    def _stay(booking_id, listing_id, **extra):
        """Build a raw in-window reservation for *listing_id*.

        Args:
            booking_id: Reservation ``_id``.
            listing_id: Value for the top-level ``listingId``.
            **extra: Additional raw reservation keys.

        Returns:
            Raw reservation dict.
        """
        return {
            "_id": booking_id,
            "status": "confirmed",
            "checkIn": _rel(-1),
            "checkOut": _rel(2),
            "listingId": listing_id,
            **extra,
        }

    @pytest.mark.asyncio
    async def test_foreign_listing_reservations_discarded(self):
        """Test only the synced listing's reservations are returned."""
        account = {
            "count": 3,
            "results": [
                self._stay("R-MINE", "L1"),
                self._stay("R-OTHER", "L2"),
                self._stay("R-THIRD", "L3"),
            ],
        }
        provider, seen = _scoped_provider({"_id": "L1", "title": "Beach"}, account)

        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert [r.pms_booking_id for r in reservations] == ["R-MINE"]
        assert json.loads(seen[0].params["filters"]) == [
            {"field": "listingId", "operator": "$in", "value": ["L1"]}
        ]

    @pytest.mark.asyncio
    async def test_multi_unit_child_reservation_retained(self):
        """Test an assigned sub-unit stay still belongs to its parent.

        Guesty computes a reservation's ``listingId`` as
        ``unitId ?? unitTypeId``, so an assigned multi-unit stay reports the
        child sub-unit rather than the parent listing being synced.
        """
        parent = {
            "_id": "P1",
            "title": "Lodge",
            "childListings": [{"_id": "C1", "title": "Unit 1"}],
        }
        account = {
            "count": 2,
            "results": [
                self._stay(
                    "R-CHILD", "C1", stay=[{"unitTypeId": "P1", "unitId": "C1"}]
                ),
                self._stay("R-FOREIGN", "C9", stay=[{"unitTypeId": "P9"}]),
            ],
        }
        provider, seen = _scoped_provider(parent, account)

        reservations = await provider.get_reservations("P1")
        await provider.aclose()

        assert [r.pms_booking_id for r in reservations] == ["R-CHILD"]
        assert reservations[0].room_ids == ("C1",)
        assert reservations[0].listing_pms_id == "P1"
        assert json.loads(seen[0].params["filters"])[0]["value"] == ["C1", "P1"]

    @pytest.mark.asyncio
    async def test_unattributable_reservation_raises(self):
        """Test a reservation naming no listing aborts the fetch.

        Accepting it is fail-open: ``_resolve_room_id()`` would substitute
        the listing being synced and export a foreign stay into the wrong
        calendar.  Dropping it is also unsafe because the sync prune logic
        cancels any booking missing from the snapshot.
        """
        orphan = self._stay("R-ORPHAN", None)
        orphan.pop("listingId")
        account = {"count": 2, "results": [self._stay("R-MINE", "L1"), orphan]}
        provider, _ = _scoped_provider({"_id": "L1", "title": "Beach"}, account)

        with pytest.raises(PMSProviderError, match="R-ORPHAN") as excinfo:
            await provider.get_reservations("L1")
        await provider.aclose()

        assert "L1" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_mismatched_reservation_excluded_quietly(self):
        """Test a reservation naming another listing is filtered, not raised.

        Only the "no usable reference whatsoever" case is exceptional; a
        record that simply belongs elsewhere is a normal exclusion.
        """
        account = {
            "count": 2,
            "results": [
                self._stay("R-MINE", "L1"),
                self._stay("R-OTHER", "L2", parentListingId="P2"),
            ],
        }
        provider, _ = _scoped_provider({"_id": "L1", "title": "Beach"}, account)

        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert [r.pms_booking_id for r in reservations] == ["R-MINE"]

    @pytest.mark.asyncio
    async def test_direct_child_listing_keeps_parent_keyed_stay(self):
        """Test syncing an imported child still sees parent-keyed stays.

        Guesty computes ``listingId`` as ``unitId ?? unitTypeId`` but some
        payloads report the parent there and name the child only in
        ``stay[].unitId``.  The server filter must therefore include the
        parent, while the ownership scope stays child-specific so a sibling
        sub-unit's stay is still excluded.  The mock honours the filter, so
        an over-narrow scope loses the record entirely.
        """
        child_doc = {"_id": "C1", "title": "Unit 1", "mtl": {"p": "P1"}}
        reservations = [
            self._stay("R-MINE", "P1", stay=[{"unitTypeId": "P1", "unitId": "C1"}]),
            self._stay("R-SIBLING", "P1", stay=[{"unitTypeId": "P1", "unitId": "C2"}]),
            self._stay("R-ELSEWHERE", "L9"),
        ]
        seen = []

        def handler(request):
            """Serve the child document and a filter-honouring page.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            if request.url.path.startswith("/v1/listings/"):
                return httpx.Response(200, json=child_doc)
            wanted = set(json.loads(request.url.params["filters"])[0]["value"])
            seen.append(wanted)
            matched = [r for r in reservations if r["listingId"] in wanted]
            return httpx.Response(200, json={"count": len(matched), "results": matched})

        token_manager = AsyncMock()
        token_manager.get_token = AsyncMock(return_value="tok")
        provider = GuestyProvider(
            token_manager=token_manager,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url=GUESTY_BASE_URL,
            ),
        )

        result = await provider.get_reservations("C1")
        await provider.aclose()

        assert seen[0] == {"C1", "P1"}
        assert [r.pms_booking_id for r in result] == ["R-MINE"]
        assert result[0].room_ids == ("C1",)

    @pytest.mark.asyncio
    async def test_scope_lookup_failure_raises(self):
        """Test a failed scope lookup aborts rather than returning data.

        Returning an unscoped or empty snapshot would make the sync prune
        logic cancel live bookings, so the fetch must fail loudly instead.
        """

        def handler(request):
            """Fail the listing lookup, serve reservations otherwise.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            if request.url.path.startswith("/v1/listings/"):
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json={"count": 0, "results": []})

        token_manager = AsyncMock()
        token_manager.get_token = AsyncMock(return_value="tok")
        provider = GuestyProvider(
            token_manager=token_manager,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url=GUESTY_BASE_URL,
            ),
        )

        with pytest.raises(PMSProviderError):
            await provider.get_reservations("L1")
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_foreign_records_still_count_toward_pagination(self):
        """Test discarded records do not fake an exhausted result set.

        ``count`` describes the raw page sequence, so records dropped as
        foreign must still advance pagination -- otherwise a truncated fetch
        would look complete and retire every unseen booking.
        """

        def handler(request):
            """Serve one full page of foreign records, then nothing.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            if request.url.path.startswith("/v1/listings/"):
                return httpx.Response(200, json={"_id": "L1", "title": "Beach"})
            skip = int(request.url.params["skip"])
            page = [self._stay(f"R{i}", "L2") for i in range(100)] if skip == 0 else []
            return httpx.Response(200, json={"count": 150, "results": page})

        token_manager = AsyncMock()
        token_manager.get_token = AsyncMock(return_value="tok")
        provider = GuestyProvider(
            token_manager=token_manager,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                base_url=GUESTY_BASE_URL,
            ),
        )

        with pytest.raises(PMSProviderError, match="partial snapshot"):
            await provider.get_reservations("L1")
        await provider.aclose()


# ---------------------------------------------------------------------------
# get_reservations pagination
# ---------------------------------------------------------------------------


def _reservation(booking_id):
    """Build a raw in-window Guesty reservation dict.

    Args:
        booking_id: Reservation ``_id``.

    Returns:
        Raw reservation dict.
    """
    return {
        "_id": booking_id,
        "status": "confirmed",
        "guest": {"_id": f"g-{booking_id}", "fullName": f"Guest {booking_id}"},
        "checkIn": _rel(-1),
        "checkOut": _rel(2),
        "listingId": "L1",
    }


def _mock_provider(handler):
    """Build a GuestyProvider wired to a MockTransport handler.

    Listing-detail requests (used by ``get_reservations`` to resolve the
    listing's sub-unit scope) are answered with a single-unit listing so the
    supplied *handler* only has to deal with reservation pages.

    Args:
        handler: Callable taking an httpx.Request and returning a Response.

    Returns:
        Configured GuestyProvider.
    """

    def dispatch(request):
        """Route listing-detail requests away from *handler*.

        Args:
            request: Outgoing request.

        Returns:
            Mocked JSON response.
        """
        if request.url.path.startswith("/v1/listings/"):
            return httpx.Response(200, json={"_id": "L1", "title": "Beach House"})
        return handler(request)

    token_manager = AsyncMock()
    token_manager.get_token = AsyncMock(return_value="tok")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(dispatch),
        base_url=GUESTY_BASE_URL,
    )
    return GuestyProvider(token_manager=token_manager, http_client=client)


class TestReservationPagination:
    """Pagination tests for GuestyProvider.get_reservations."""

    @pytest.mark.asyncio
    async def test_accumulates_pages_driven_by_count(self):
        """Test every record is fetched when ``count`` exceeds one page."""
        total = 250
        requested_skips = []

        def handler(request):
            """Serve full pages up to ``total`` records.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            skip = int(request.url.params["skip"])
            requested_skips.append(skip)
            page = [_reservation(f"R{i}") for i in range(skip, min(skip + 100, total))]
            return httpx.Response(200, json={"count": total, "results": page})

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(reservations) == total
        assert requested_skips == [0, 100, 200]

    @pytest.mark.asyncio
    async def test_short_page_is_not_treated_as_end(self):
        """Test a short non-final page does not truncate the snapshot.

        A server-side page cap smaller than the requested limit must not be
        mistaken for end-of-results: the sync prune logic treats missing
        bookings as cancelled, so truncation silently retires live stays.
        """
        total = 120
        page_size = 40

        def handler(request):
            """Serve 40-record pages even though 100 are requested.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            skip = int(request.url.params["skip"])
            page = [
                _reservation(f"R{i}") for i in range(skip, min(skip + page_size, total))
            ]
            return httpx.Response(200, json={"count": total, "results": page})

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(reservations) == total

    @pytest.mark.asyncio
    async def test_later_page_failure_returns_nothing(self):
        """Test a failure on a later page raises instead of truncating."""

        def handler(request):
            """Fail the second page.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            skip = int(request.url.params["skip"])
            if skip == 0:
                page = [_reservation(f"R{i}") for i in range(100)]
                return httpx.Response(200, json={"count": 200, "results": page})
            return httpx.Response(500, json={"error": "boom"})

        provider = _mock_provider(handler)
        with pytest.raises(PMSProviderError):
            await provider.get_reservations("L1")
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_missing_count_falls_back_to_short_page(self):
        """Test the short-page heuristic is used when ``count`` is absent."""
        pages = [
            {"results": [_reservation(f"R{i}") for i in range(100)]},
            {"results": [_reservation("R100")]},
        ]
        calls = []

        def handler(request):
            """Serve the prepared pages without ``count`` metadata.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            index = len(calls)
            calls.append(request)
            return httpx.Response(200, json=pages[index])

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(calls) == 2
        assert len(reservations) == 101

    @pytest.mark.asyncio
    async def test_empty_page_raises_instead_of_partial(self):
        """Test an inconsistent ``count`` raises rather than truncating.

        An empty page while records remain outstanding means the API will not
        serve the rest of the snapshot.  Returning the records gathered so far
        would let the sync prune logic cancel every unseen future booking, so
        the run must fail and preserve the existing snapshot instead.
        """
        calls = []

        def handler(request):
            """Claim more records than are ever served.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request)
            if request.url.params["skip"] != "0":
                return httpx.Response(200, json={"count": 9999, "results": []})
            page = [_reservation(f"R{i}") for i in range(100)]
            return httpx.Response(200, json={"count": 9999, "results": page})

        provider = _mock_provider(handler)
        with pytest.raises(PMSProviderError, match="partial snapshot"):
            await provider.get_reservations("L1")
        await provider.aclose()

        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_exact_count_completes_without_empty_page(self):
        """Test a run ending exactly on ``count`` does not raise.

        The empty-page guard must not turn a legitimate completion into a
        spurious error when the final page lands exactly on ``count``.
        """
        total = 200
        calls = []

        def handler(request):
            """Serve exactly ``total`` records in full pages.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request)
            skip = int(request.url.params["skip"])
            page = [_reservation(f"R{i}") for i in range(skip, min(skip + 100, total))]
            return httpx.Response(200, json={"count": total, "results": page})

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(calls) == 2
        assert len(reservations) == total

    @pytest.mark.asyncio
    async def test_missing_count_empty_first_page_returns_empty(self):
        """Test the ``count``-less fallback still ends on an empty page."""
        calls = []

        def handler(request):
            """Serve a single empty page without ``count`` metadata.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request)
            return httpx.Response(200, json={"results": []})

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(calls) == 1
        assert reservations == []

    @pytest.mark.asyncio
    async def test_zero_count_returns_empty(self):
        """Test a listing with no reservations completes cleanly."""

        def handler(request):
            """Report zero records.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(200, json={"count": 0, "results": []})

        provider = _mock_provider(handler)
        reservations = await provider.get_reservations("L1")
        await provider.aclose()

        assert reservations == []

    @pytest.mark.asyncio
    async def test_growing_count_raises_instead_of_partial(self):
        """Test a ``count`` that grows mid-pagination raises.

        A concurrent insertion into the unread portion of the result set
        shifts records across page boundaries, so the pages already read and
        the pages still to come describe different snapshots.  Trusting the
        first ``count`` would stop early and omit a real tail booking, which
        the sync prune logic would then cancel and drop from the iCal feed.
        """
        calls = []

        def handler(request):
            """Report 200 records, then 201 on the second page.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request)
            skip = int(request.url.params["skip"])
            total = 200 if skip == 0 else 201
            page = [_reservation(f"R{i}") for i in range(skip, skip + 100)]
            return httpx.Response(200, json={"count": total, "results": page})

        provider = _mock_provider(handler)
        with pytest.raises(PMSProviderError, match="count changed during") as excinfo:
            await provider.get_reservations("L1")
        await provider.aclose()

        assert len(calls) == 2
        message = str(excinfo.value)
        assert "200" in message
        assert "201" in message

    @pytest.mark.asyncio
    async def test_shrinking_count_raises_instead_of_partial(self):
        """Test a ``count`` that shrinks mid-pagination raises.

        A concurrent deletion is equally destructive: the remaining pages are
        renumbered against a smaller result set, so records can be skipped
        entirely and would be retired as unseen.
        """
        calls = []

        def handler(request):
            """Report 200 records, then 150 on the second page.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request)
            skip = int(request.url.params["skip"])
            total = 200 if skip == 0 else 150
            page = [_reservation(f"R{i}") for i in range(skip, skip + 50)]
            return httpx.Response(200, json={"count": total, "results": page})

        provider = _mock_provider(handler)
        with pytest.raises(PMSProviderError, match="count changed during") as excinfo:
            await provider.get_reservations("L1")
        await provider.aclose()

        assert len(calls) == 2
        message = str(excinfo.value)
        assert "200" in message
        assert "150" in message

    @pytest.mark.asyncio
    async def test_page_cap_raises(self, monkeypatch):
        """Test exceeding the page cap raises instead of truncating."""
        import src.providers.guesty.service as guesty_service

        monkeypatch.setattr(guesty_service, "MAX_RESERVATION_PAGES", 3)

        def handler(request):
            """Always claim there is more to fetch.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            skip = int(request.url.params["skip"])
            page = [_reservation(f"R{skip + i}") for i in range(100)]
            return httpx.Response(200, json={"count": 100_000, "results": page})

        provider = _mock_provider(handler)
        with pytest.raises(PMSProviderError, match="exceeded 3 pages"):
            await provider.get_reservations("L1")
        await provider.aclose()


class TestReservationWindowBounds:
    """Window-bound handling for GuestyProvider.get_reservations.

    The provider interface does not require timezone-aware bounds, and the
    window filter is now applied client-side against UTC-aware parsed dates,
    so naive bounds must be coerced rather than raising ``TypeError``.
    """

    @staticmethod
    def _single_page_provider():
        """Build a provider serving one in-window reservation.

        Returns:
            Configured GuestyProvider.
        """

        def handler(request):
            """Serve a single reservation page.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(
                200, json={"count": 1, "results": [_reservation("R1")]}
            )

        return _mock_provider(handler)

    @pytest.mark.asyncio
    async def test_aware_bounds(self):
        """Test timezone-aware bounds select the overlapping stay."""
        now = datetime.now(UTC)
        provider = self._single_page_provider()
        reservations = await provider.get_reservations(
            "L1",
            start_date=now - timedelta(days=10),
            end_date=now + timedelta(days=10),
        )
        await provider.aclose()

        assert len(reservations) == 1

    @pytest.mark.asyncio
    async def test_naive_bounds(self):
        """Test naive bounds are treated as UTC instead of raising."""
        now = datetime.now(UTC).replace(tzinfo=None)
        provider = self._single_page_provider()
        reservations = await provider.get_reservations(
            "L1",
            start_date=now - timedelta(days=10),
            end_date=now + timedelta(days=10),
        )
        await provider.aclose()

        assert len(reservations) == 1

    @pytest.mark.asyncio
    async def test_mixed_naive_and_aware_bounds(self):
        """Test a naive start combined with an aware end is accepted."""
        now = datetime.now(UTC)
        provider = self._single_page_provider()
        reservations = await provider.get_reservations(
            "L1",
            start_date=(now - timedelta(days=10)).replace(tzinfo=None),
            end_date=now + timedelta(days=10),
        )
        await provider.aclose()

        assert len(reservations) == 1

    @pytest.mark.asyncio
    async def test_naive_bounds_still_filter(self):
        """Test naive bounds outside the stay still exclude it."""
        now = datetime.now(UTC).replace(tzinfo=None)
        provider = self._single_page_provider()
        reservations = await provider.get_reservations(
            "L1",
            start_date=now - timedelta(days=60),
            end_date=now - timedelta(days=30),
        )
        await provider.aclose()

        assert reservations == []


# ---------------------------------------------------------------------------
# lastUpdatedAt change-detection signal (issue #128)
# ---------------------------------------------------------------------------


class TestReservationUpdatedAt:
    """Tests for the reservation change-detection timestamp."""

    def test_last_updated_at_is_requested(self):
        """Test the projection names lastUpdatedAt.

        Guesty returns only the paths named in ``fields``, so the sync's
        change detection silently dies if this path is dropped.
        """
        assert "lastUpdatedAt" in RESERVATION_FIELDS.split(" ")

    def test_last_updated_at_mapped(self):
        """Test lastUpdatedAt is surfaced as PMSReservation.updated_at."""
        result = GuestyProvider._map_reservation(
            {
                "_id": "R_TS",
                "status": "confirmed",
                "checkIn": _rel(40),
                "checkOut": _rel(44),
                "lastUpdatedAt": "2026-08-17T07:00:00.000Z",
            },
            "L1",
        )

        assert result is not None
        assert result.updated_at == datetime(2026, 8, 17, 7, 0, tzinfo=UTC)

    @pytest.mark.parametrize("value", [None, "", "not-a-date"])
    def test_unusable_last_updated_at_is_none(self, value):
        """Test an absent or malformed timestamp never aborts mapping."""
        result = GuestyProvider._map_reservation(
            {
                "_id": "R_TS",
                "status": "confirmed",
                "checkIn": _rel(40),
                "checkOut": _rel(44),
                "lastUpdatedAt": value,
            },
            "L1",
        )

        assert result is not None
        assert result.updated_at is None


# ---------------------------------------------------------------------------
# get_rooms
# ---------------------------------------------------------------------------


class TestGetRooms:
    """Tests for GuestyProvider.get_rooms."""

    @pytest.mark.asyncio
    async def test_multi_unit_listing(self, guesty_provider, mock_http_client):
        """Test that child listings map to PMSRoom DTOs."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_LISTING_MULTI_UNIT)
        )

        rooms = await guesty_provider.get_rooms("L1")

        assert len(rooms) == 2
        assert all(isinstance(r, PMSRoom) for r in rooms)
        assert rooms[0].pms_room_id == "C1"
        assert rooms[0].name == "Suite A"
        assert rooms[0].room_type == "suite"
        assert rooms[1].pms_room_id == "C2"
        assert rooms[1].name == "Room B"

    @pytest.mark.asyncio
    async def test_single_unit_listing(self, guesty_provider, mock_http_client):
        """Test that single-unit creates one implicit room."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_LISTING_SINGLE_UNIT)
        )

        rooms = await guesty_provider.get_rooms("L1")

        assert len(rooms) == 1
        assert rooms[0].pms_room_id == "L1"
        assert rooms[0].name == "Beach House"
        assert rooms[0].room_type == "apartment"

    @pytest.mark.asyncio
    async def test_no_child_listings_key(self, guesty_provider, mock_http_client):
        """Test fallback when childListings key is absent."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(
                {
                    "_id": "L5",
                    "title": "Simple Place",
                    "propertyType": "house",
                }
            )
        )

        rooms = await guesty_provider.get_rooms("L5")
        assert len(rooms) == 1
        assert rooms[0].pms_room_id == "L5"


# ---------------------------------------------------------------------------
# get_guest
# ---------------------------------------------------------------------------


class TestGetGuest:
    """Tests for GuestyProvider.get_guest."""

    @pytest.mark.asyncio
    async def test_returns_guest(self, guesty_provider, mock_http_client):
        """Test that guest data is returned as PMSGuest."""
        mock_http_client.request = AsyncMock(return_value=_make_response(SAMPLE_GUEST))

        guest = await guesty_provider.get_guest("G1")

        assert guest is not None
        assert isinstance(guest, PMSGuest)
        assert guest.guest_id == "G1"
        assert guest.full_name == "Alice Smith"
        assert guest.phone == "+15551234567"
        assert guest.email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self, guesty_provider, mock_http_client):
        """Test that a 404 returns None."""
        mock_http_client.request = AsyncMock(
            return_value=httpx.Response(
                status_code=404,
                text="Not Found",
                request=httpx.Request("GET", f"{GUESTY_BASE_URL}/v1/guests/UNKNOWN"),
            )
        )

        guest = await guesty_provider.get_guest("UNKNOWN")
        assert guest is None

    @pytest.mark.asyncio
    async def test_raises_on_non_404_error(self, guesty_provider, mock_http_client):
        """Test that non-404 errors raise PMSProviderError."""
        mock_http_client.request = AsyncMock(
            return_value=httpx.Response(
                status_code=500,
                text="Server Error",
                request=httpx.Request("GET", f"{GUESTY_BASE_URL}/v1/guests/G1"),
            )
        )

        with pytest.raises(PMSProviderError):
            await guesty_provider.get_guest("G1")


# ---------------------------------------------------------------------------
# Custom fields (v3 endpoint)
# ---------------------------------------------------------------------------


class TestGetCustomFields:
    """Tests for GuestyProvider.get_custom_fields."""

    @pytest.mark.asyncio
    async def test_returns_field_map(self, guesty_provider, mock_http_client):
        """Test that custom fields are returned as a dict."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_CUSTOM_FIELDS)
        )

        fields = await guesty_provider.get_custom_fields("R1")

        assert fields == {
            "cf_1": "early_checkin",
            "cf_2": "ground_floor",
        }

    @pytest.mark.asyncio
    async def test_empty_custom_fields(self, guesty_provider, mock_http_client):
        """Test that empty custom fields return empty dict."""
        mock_http_client.request = AsyncMock(return_value=_make_response([]))

        fields = await guesty_provider.get_custom_fields("R1")
        assert fields == {}

    @pytest.mark.asyncio
    async def test_dict_response_with_results(self, guesty_provider, mock_http_client):
        """Test fallback parsing for dict-wrapped results."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(
                {
                    "results": [
                        {"fieldId": "f1", "value": "v1"},
                    ]
                }
            )
        )

        fields = await guesty_provider.get_custom_fields("R1")
        assert fields == {"f1": "v1"}


# ---------------------------------------------------------------------------
# 429 retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    """Tests for HTTP 429 retry logic."""

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_429(self, _mock_sleep, guesty_provider, mock_http_client):
        """Test that 429 responses trigger retries."""
        rate_limit_resp = httpx.Response(
            status_code=429,
            text="Too Many Requests",
            headers={"Retry-After": "0.01"},
            request=httpx.Request("GET", f"{GUESTY_BASE_URL}/test"),
        )
        success_resp = _make_response({"results": []})

        mock_http_client.request = AsyncMock(
            side_effect=[rate_limit_resp, success_resp]
        )

        response = await guesty_provider._request("GET", "/test")
        assert response.status_code == 200
        assert mock_http_client.request.call_count == 2

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_raises_after_max_retries(
        self, _mock_sleep, guesty_provider, mock_http_client
    ):
        """Test that PMSRateLimitError is raised after max retries."""
        rate_limit_resp = httpx.Response(
            status_code=429,
            text="Too Many Requests",
            headers={"Retry-After": "0.01"},
            request=httpx.Request("GET", f"{GUESTY_BASE_URL}/test"),
        )

        mock_http_client.request = AsyncMock(return_value=rate_limit_resp)

        with pytest.raises(PMSRateLimitError, match="rate limit exceeded"):
            await guesty_provider._request("GET", "/test")

        assert mock_http_client.request.call_count == 4

    @pytest.mark.asyncio
    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_after_header_parsed(
        self, _mock_sleep, guesty_provider, mock_http_client
    ):
        """Test that Retry-After header is respected."""
        rate_limit_resp = httpx.Response(
            status_code=429,
            text="Too Many Requests",
            headers={"Retry-After": "0.01"},
            request=httpx.Request("GET", f"{GUESTY_BASE_URL}/test"),
        )
        success_resp = _make_response({"ok": True})

        mock_http_client.request = AsyncMock(
            side_effect=[rate_limit_resp, success_resp]
        )

        response = await guesty_provider._request("GET", "/test")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_auth_error_not_retried(self, guesty_provider, mock_http_client):
        """Test that auth errors are not retried."""
        mock_http_client.request = AsyncMock(
            return_value=httpx.Response(
                status_code=401,
                text="Unauthorized",
                request=httpx.Request("GET", f"{GUESTY_BASE_URL}/test"),
            )
        )

        with pytest.raises(PMSAuthenticationError):
            await guesty_provider._request("GET", "/test")

        assert mock_http_client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_network_error_raises_connection_error(
        self, guesty_provider, mock_http_client
    ):
        """Test that network errors raise PMSConnectionError."""
        mock_http_client.request = AsyncMock(side_effect=httpx.ConnectError("DNS fail"))

        with pytest.raises(PMSConnectionError, match="DNS fail"):
            await guesty_provider._request("GET", "/test")


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------


class TestRefreshToken:
    """Tests for GuestyProvider.refresh_token."""

    @pytest.mark.asyncio
    async def test_refresh_delegates_to_token_manager(
        self, guesty_provider, mock_token_manager
    ):
        """Test that refresh delegates to token manager."""
        mock_credential = MagicMock()
        result = await guesty_provider.refresh_token(mock_credential)

        mock_token_manager.invalidate_cache.assert_awaited_once()
        assert result.access_token == "test-token"
        assert result.refresh_token is None

    @pytest.mark.asyncio
    async def test_refresh_without_manager_raises(self, mock_http_client):
        """Test that refresh without manager and missing creds raises."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()
        mock_credential.client_id = None
        mock_credential.client_secret = None

        with pytest.raises(PMSAuthenticationError, match="client_id"):
            await prov.refresh_token(mock_credential)

    @pytest.mark.asyncio
    async def test_refresh_direct_http_success(self, mock_http_client):
        """Test direct HTTP refresh returns TokenResult on 200."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()
        mock_credential.client_id = "cid"
        mock_credential.client_secret = "csecret"

        resp = httpx.Response(
            status_code=200,
            json={"access_token": "new-tok", "expires_in": 3600},
            request=httpx.Request("POST", "https://example.com"),
        )
        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.post = AsyncMock(return_value=resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await prov.refresh_token(mock_credential)

        assert result.access_token == "new-tok"
        assert result.refresh_token is None
        assert result.expires_at is not None

    @pytest.mark.asyncio
    async def test_refresh_direct_http_non_200(self, mock_http_client):
        """Test direct HTTP refresh raises on non-200 response."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()
        mock_credential.client_id = "cid"
        mock_credential.client_secret = "csecret"

        resp = httpx.Response(
            status_code=500,
            text="Internal Server Error",
            request=httpx.Request("POST", "https://example.com"),
        )
        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.post = AsyncMock(return_value=resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(PMSAuthenticationError, match="failed"):
                await prov.refresh_token(mock_credential)

    @pytest.mark.asyncio
    async def test_refresh_direct_http_missing_access_token(self, mock_http_client):
        """Test direct HTTP refresh raises when access_token missing."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()
        mock_credential.client_id = "cid"
        mock_credential.client_secret = "csecret"

        resp = httpx.Response(
            status_code=200,
            json={"expires_in": 3600},
            request=httpx.Request("POST", "https://example.com"),
        )
        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.post = AsyncMock(return_value=resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(PMSAuthenticationError, match="missing access_token"):
                await prov.refresh_token(mock_credential)

    @pytest.mark.asyncio
    async def test_refresh_direct_http_network_error(self, mock_http_client):
        """Test direct HTTP refresh raises on network error."""
        prov = GuestyProvider(http_client=mock_http_client)
        mock_credential = MagicMock()
        mock_credential.client_id = "cid"
        mock_credential.client_secret = "csecret"

        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(PMSConnectionError, match="Failed to connect"):
                await prov.refresh_token(mock_credential)


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


class TestTestConnection:
    """Tests for GuestyProvider.test_connection."""

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, guesty_provider, mock_http_client):
        """Test that test_connection returns True on success."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response(SAMPLE_LISTINGS_PAGE1)
        )

        assert await guesty_provider.test_connection() is True

    @pytest.mark.asyncio
    async def test_warns_on_zero_listings(
        self, guesty_provider, mock_http_client, caplog
    ):
        """Test that zero listings produces a warning."""
        mock_http_client.request = AsyncMock(
            return_value=_make_response({"results": []})
        )

        import logging

        with caplog.at_level(logging.WARNING):
            result = await guesty_provider.test_connection()

        assert result is True
        assert "zero listings" in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_raises_on_failure(self, guesty_provider, mock_http_client):
        """Test that connection failures raise PMSConnectionError."""
        mock_http_client.request = AsyncMock(
            side_effect=httpx.ConnectError("unreachable")
        )

        with pytest.raises(PMSConnectionError):
            await guesty_provider.test_connection()


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestParseDate:
    """Tests for _parse_date helper."""

    def test_parses_iso_datetime(self):
        """Test parsing ISO datetime strings."""
        result = _parse_date("2026-03-01T14:00:00Z")
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 1

    def test_parses_date_only(self):
        """Test parsing date-only strings."""
        result = _parse_date("2026-03-01")
        assert result == datetime(2026, 3, 1, tzinfo=UTC)

    def test_passes_through_datetime(self):
        """Test that datetime objects pass through."""
        dt = datetime(2026, 3, 1, tzinfo=UTC)
        assert _parse_date(dt) == dt

    def test_none_raises(self):
        """Test that None raises PMSProviderError."""
        with pytest.raises(PMSProviderError):
            _parse_date(None)

    def test_empty_raises(self):
        """Test that empty string raises PMSProviderError."""
        with pytest.raises(PMSProviderError):
            _parse_date("")


class TestParseRetryAfter:
    """Tests for _parse_retry_after helper."""

    def test_parses_numeric(self):
        """Test parsing numeric Retry-After header."""
        resp = httpx.Response(
            status_code=429,
            headers={"Retry-After": "30"},
            request=httpx.Request("GET", "https://example.com"),
        )
        assert _parse_retry_after(resp) == 30.0

    def test_returns_none_when_missing(self):
        """Test None when Retry-After header is absent."""
        resp = httpx.Response(
            status_code=429,
            request=httpx.Request("GET", "https://example.com"),
        )
        assert _parse_retry_after(resp) is None

    def test_returns_none_on_invalid(self):
        """Test None for non-numeric Retry-After."""
        resp = httpx.Response(
            status_code=429,
            headers={"Retry-After": "not-a-number"},
            request=httpx.Request("GET", "https://example.com"),
        )
        assert _parse_retry_after(resp) is None


class TestFormatAddress:
    """Tests for _format_address helper."""

    def test_full_address(self):
        """Test formatting a full address string."""
        assert _format_address({"full": "123 Main St"}) == "123 Main St"

    def test_partial_address(self):
        """Test formatting a partial address."""
        result = _format_address({"street": "123 Main", "city": "NYC"})
        assert result == "123 Main, NYC"

    def test_none_address(self):
        """Test that None address returns None."""
        assert _format_address(None) is None

    def test_empty_address(self):
        """Test that empty dict returns None."""
        assert _format_address({}) is None


class TestExtractCustomData:
    """Tests for _extract_custom_data helper."""

    def test_top_level_keys(self):
        """Test extraction of top-level reservation fields."""
        raw = {
            "source": "booking.com",
            "confirmationCode": "XYZ",
            "nightsCount": 3,
            "guestsCount": 1,
        }
        result = _extract_custom_data(raw)
        assert result == {
            "source": "booking.com",
            "confirmationCode": "XYZ",
            "nightsCount": 3,
            "guestsCount": 1,
        }

    def test_money_fields(self):
        """Test extraction of nested money fields."""
        raw = {
            "money": {
                "totalPaid": 250.50,
                "balanceDue": 0,
                "currency": "EUR",
            },
        }
        result = _extract_custom_data(raw)
        assert result["money_totalPaid"] == 250.50
        assert result["money_balanceDue"] == 0
        assert result["money_currency"] == "EUR"

    def test_notes_from_dict_objects(self):
        """Test notes extraction from dict note objects."""
        raw = {
            "notes": [
                {"note": "Early check-in"},
                {"note": "Allergic to cats"},
            ],
        }
        result = _extract_custom_data(raw)
        assert result["notes"] == "Early check-in; Allergic to cats"

    def test_notes_from_strings(self):
        """Test notes extraction from string entries."""
        raw = {"notes": ["First note", "Second note"]}
        result = _extract_custom_data(raw)
        assert result["notes"] == "First note; Second note"

    def test_empty_notes_not_included(self):
        """Test that empty notes list produces no key."""
        assert "notes" not in _extract_custom_data({"notes": []})
        assert "notes" not in _extract_custom_data({"notes": [{"note": ""}]})

    def test_none_values_skipped(self):
        """Test that None field values are not included."""
        raw = {"source": None, "confirmationCode": "OK"}
        result = _extract_custom_data(raw)
        assert "source" not in result
        assert result["confirmationCode"] == "OK"

    def test_empty_raw_returns_empty(self):
        """Test that empty raw dict returns empty custom_data."""
        assert _extract_custom_data({}) == {}

    def test_non_dict_money_ignored(self):
        """Test that non-dict money value is ignored."""
        assert _extract_custom_data({"money": "invalid"}) == {}


# ---------------------------------------------------------------------------
# Field projection and status-classification safety nets (issue #121)
# ---------------------------------------------------------------------------


# Guesty's documented DEFAULT projection for GET /v1/reservations — what the
# API really returns when ``fields`` is omitted.  Note the absence of
# ``status``: this fixture is reality, every other fixture in this suite is an
# idealised payload.
# https://open-api-docs.guesty.com/docs/how-to-search-for-reservations
SAMPLE_DEFAULT_PROJECTION_RESERVATION = {
    "_id": "R-DEFAULT",
    "accountId": "acct-1",
    "guestId": "G1",
    "listingId": "L1",
    "listing": {"_id": "L1", "title": "Beach House"},
    "checkIn": _rel(1),
    "checkOut": _rel(4),
    "guest": {"_id": "G1", "fullName": "Alice Smith"},
    "confirmationCode": "ABC123",
}


def _status_reservation(booking_id, status):
    """Build an in-window reservation carrying *status*.

    Args:
        booking_id: Reservation ``_id``.
        status: Raw Guesty status value.

    Returns:
        Raw reservation dict.
    """
    raw = _reservation(booking_id)
    raw["status"] = status
    return raw


class TestReservationFieldProjection:
    """The ``fields`` projection sent to GET /v1/reservations."""

    @pytest.mark.asyncio
    async def test_request_sends_fields_including_status(self):
        """Test the reservation request asks for ``status`` explicitly.

        Regression guard for the v0.5.2 outage: omitting ``fields`` makes
        Guesty return its default projection, which has no ``status``.
        """
        seen = []

        def handler(request):
            """Record the query parameters of each reservation request.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            seen.append(dict(request.url.params))
            return httpx.Response(
                200, json={"count": 1, "results": [_reservation("R1")]}
            )

        provider = _mock_provider(handler)
        await provider.get_reservations("L1")
        await provider.aclose()

        assert seen, "no reservation request was made"
        fields = seen[0]["fields"]
        # Space separated, per the reservationsV2List OpenAPI schema.
        assert "status" in fields.split(" ")

    def test_projection_covers_every_consumed_path(self):
        """Test the projection names every path the parser reads.

        Any field we consume but do not request is silently stripped by
        Guesty once ``fields`` is specified, which is exactly how the
        outage hid itself.  This fails if a new top-level ``raw.get()`` or
        a new key tuple is added without updating the constant.
        """
        source = Path(service.__file__).read_text(encoding="utf-8")
        reservation_section = source.split("# -- module-level helpers")[0]
        consumed = set(re.findall(r'raw\.get\(\s*"([A-Za-z_]+)"', reservation_section))
        consumed |= set(service._LISTING_REF_KEYS)
        consumed |= set(service._CUSTOM_KEYS)
        consumed |= {"money", "notes", "stay", "mtl", "guest", "status"}

        missing = consumed - set(service._RESERVATION_FIELD_PATHS)
        assert not missing, (
            f"fields projection is missing consumed path(s): {sorted(missing)}"
        )

    def test_projection_names_nested_paths_individually(self):
        """Test nested paths are named, not just their parents.

        Guesty truncates nested objects to ``_id`` once ``fields`` is
        specified, so ``guest.fullName`` and the room-assignment paths must
        appear in their own right.
        """
        paths = set(service._RESERVATION_FIELD_PATHS)
        for required in (
            "guest.fullName",
            "guest._id",
            "stay.unitId",
            "mtl._id",
            "mtl.assigned",
            "money.totalPaid",
        ):
            assert required in paths
            # A dotted path must come with its parent object.
            assert required.split(".")[0] in paths


class TestMissingStatusKey:
    """A payload with no ``status`` key is a systemic projection failure."""

    def test_absent_status_raises(self):
        """Test an absent ``status`` key raises instead of defaulting."""
        with pytest.raises(PMSProviderError, match="no 'status' field"):
            GuestyProvider._map_reservation(
                SAMPLE_DEFAULT_PROJECTION_RESERVATION,
                "L1",
            )

    def test_absent_status_names_the_reservation(self):
        """Test the error names the offending reservation ID."""
        with pytest.raises(PMSProviderError, match="R-DEFAULT"):
            GuestyProvider._map_reservation(
                SAMPLE_DEFAULT_PROJECTION_RESERVATION,
                "L1",
            )

    def test_absent_status_never_defaults_to_confirmed(self):
        """Test no reservation is silently treated as confirmed.

        Defaulting is fail-open: a cancelled guest would keep a working
        door code.  A raised error is fail-closed.
        """
        try:
            result = GuestyProvider._map_reservation(
                SAMPLE_DEFAULT_PROJECTION_RESERVATION,
                "L1",
            )
        except PMSProviderError:
            return
        pytest.fail(f"expected PMSProviderError, got {result!r}")

    @pytest.mark.asyncio
    async def test_default_projection_page_aborts_the_sync(self):
        """Test a default-projection page aborts rather than empties.

        Aborting means SyncService never gets a snapshot, never prunes, and
        the last-good bookings keep serving.
        """

        def handler(request):
            """Serve one Guesty default-projection page.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [SAMPLE_DEFAULT_PROJECTION_RESERVATION],
                },
            )

        provider = _mock_provider(handler)
        with pytest.raises(PMSProviderError, match="default projection"):
            await provider.get_reservations("L1")
        await provider.aclose()

    def test_present_but_unrecognised_is_withheld_not_raised(self):
        """Test an unrecognised value keeps the v0.5.2 withhold behaviour."""
        result = GuestyProvider._map_reservation(
            _status_reservation("R-ODD", "awaiting_payment"),
            "L1",
        )

        assert result is not None
        assert result.status == "awaiting_payment"

    def test_null_status_value_is_withheld_not_raised(self):
        """Test ``status: null`` is a present-but-unusable value."""
        result = GuestyProvider._map_reservation(
            _status_reservation("R-NULL", None),
            "L1",
        )

        assert result is not None
        assert result.status == ""


class TestUnclassifiedSystemicGuard:
    """Mass-unclassified snapshots are refused rather than exported."""

    @staticmethod
    def _serve(reservations):
        """Build a provider serving *reservations* as one page.

        Args:
            reservations: Raw reservation dicts.

        Returns:
            Configured GuestyProvider.
        """

        def handler(request):
            """Serve the canned reservation page.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(
                200,
                json={"count": len(reservations), "results": reservations},
            )

        return _mock_provider(handler)

    @pytest.mark.asyncio
    async def test_majority_unclassified_raises(self):
        """Test a mostly-unrecognised snapshot is refused."""
        reservations = [_status_reservation(f"R{i}", "mystery") for i in range(4)]
        provider = self._serve(reservations)

        with pytest.raises(PMSProviderError, match="status-mapping failure"):
            await provider.get_reservations("L1")
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_small_account_does_not_trip_the_guard(self):
        """Test a 3-reservation account is never judged systemic.

        A tiny account can legitimately be 100% odd statuses, so the guard
        needs a minimum sample before a ratio means anything.
        """
        reservations = [_status_reservation(f"R{i}", "mystery") for i in range(3)]
        provider = self._serve(reservations)

        result = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(result) == 3
        assert {r.status for r in result} == {"mystery"}

    @pytest.mark.asyncio
    async def test_minority_unclassified_is_tolerated(self):
        """Test a few odd statuses in a healthy account still sync."""
        reservations = [_status_reservation(f"R{i}", "confirmed") for i in range(8)]
        reservations.append(_status_reservation("R-ODD", "mystery"))
        provider = self._serve(reservations)

        result = await provider.get_reservations("L1")
        await provider.aclose()

        assert len(result) == 9

    @pytest.mark.asyncio
    async def test_guard_counts_across_pages(self):
        """Test the guard sees the whole snapshot, not a single page."""
        pages = [
            [_status_reservation(f"A{i}", "mystery") for i in range(100)],
            [_status_reservation("B0", "confirmed")],
        ]

        def handler(request):
            """Serve two reservation pages.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            skip = int(request.url.params["skip"])
            page = pages[0] if skip == 0 else pages[1]
            return httpx.Response(200, json={"count": 101, "results": page})

        provider = _mock_provider(handler)
        with pytest.raises(PMSProviderError, match="status-mapping failure"):
            await provider.get_reservations("L1")
        await provider.aclose()


def _out_of_window_reservation(booking_id, status):
    """Build a long-past reservation carrying *status*.

    ``get_reservations`` sends no stay-date filter, so Guesty returns the
    listing's entire history and these records are filtered client-side.

    Args:
        booking_id: Reservation ``_id``.
        status: Raw Guesty status value.

    Returns:
        Raw reservation dict outside the default stay window.
    """
    raw = _status_reservation(booking_id, status)
    raw["checkIn"] = _rel(-900)
    raw["checkOut"] = _rel(-897)
    return raw


class TestRecognisedNonExportStatuses:
    """Documented non-stay states are withheld, not treated as failures."""

    @pytest.mark.asyncio
    async def test_mixed_real_world_statuses_do_not_abort(self):
        """Test a realistic declined/expired/closed mix still syncs.

        A normal account accumulates these, so counting them as evidence of
        a broken status mapping would abort every sync permanently and the
        cancellation below would never reach SyncService.
        """
        reservations = [
            _status_reservation("R-DECLINED", "declined"),
            _status_reservation("R-EXPIRED", "expired"),
            _status_reservation("R-CLOSED", "closed"),
            _status_reservation("R-CANCELED", "canceled"),
        ]
        provider = TestUnclassifiedSystemicGuard._serve(reservations)

        result = await provider.get_reservations("L1")
        await provider.aclose()

        statuses = {r.pms_booking_id: r.status for r in result}
        assert statuses["R-CANCELED"] == "cancelled"
        assert len(result) == 4

    @pytest.mark.parametrize(
        "status",
        ["expired", "closed", "declined", "awaiting_payment"],
    )
    def test_non_stay_state_is_withheld_verbatim(self, status):
        """Test the DTO survives carrying its raw status.

        Returning None would drop the record from the snapshot and the sync
        prune loop would retire it as cancelled; policy is withhold, not
        retire.

        Args:
            status: Raw Guesty status value.
        """
        result = GuestyProvider._map_reservation(
            _status_reservation(f"R-{status}", status),
            "L1",
        )

        assert result is not None
        assert result.status == status

    def test_non_stay_state_does_not_warn(self, caplog):
        """Test an expected non-stay state logs at DEBUG, not WARNING."""
        with caplog.at_level(logging.DEBUG):
            GuestyProvider._map_reservation(
                _status_reservation("R-DECLINED", "declined"),
                "L1",
            )

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("known non-stay state" in r.message for r in caplog.records)

    def test_genuinely_unknown_status_still_warns(self, caplog):
        """Test real drift keeps its WARNING signal."""
        with caplog.at_level(logging.DEBUG):
            GuestyProvider._map_reservation(
                _status_reservation("R-ODD", "zzz_unknown"),
                "L1",
            )

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings
        assert "per-booking issue" in warnings[0].message


class TestGuardCountsOnlyInWindow:
    """The guard judges the returned snapshot, not the whole history."""

    @pytest.mark.asyncio
    async def test_out_of_window_unknowns_do_not_trip_the_guard(self):
        """Test ancient odd records cannot abort a healthy current sync.

        Counting history would let four stale unknowns plus one current
        cancellation abort the sync, stranding a revoked booking in the
        exported calendar.
        """
        reservations = [
            _out_of_window_reservation(f"OLD{i}", "zzz_unknown") for i in range(4)
        ]
        reservations.append(_status_reservation("R-CANCELED", "canceled"))
        provider = TestUnclassifiedSystemicGuard._serve(reservations)

        result = await provider.get_reservations("L1")
        await provider.aclose()

        assert [r.pms_booking_id for r in result] == ["R-CANCELED"]
        assert result[0].status == "cancelled"

    @pytest.mark.asyncio
    async def test_historical_confirmed_cannot_mask_a_broken_window(self):
        """Test a pile of old confirmed records does not defang the guard.

        The current window is entirely unknown here, which is exactly the
        mapping failure the guard exists to catch.
        """
        reservations = [
            _out_of_window_reservation(f"OLD{i}", "confirmed") for i in range(50)
        ]
        reservations += [
            _status_reservation(f"NEW{i}", "zzz_unknown") for i in range(4)
        ]
        provider = TestUnclassifiedSystemicGuard._serve(reservations)

        with pytest.raises(PMSProviderError, match="status-mapping failure"):
            await provider.get_reservations("L1")
        await provider.aclose()


# ---------------------------------------------------------------------------
# Custom field definitions (issue #127)
# ---------------------------------------------------------------------------


def _definitions_provider(
    handler,
    *,
    credential_id: int = 1,
    client_id: str = "client-a",
    token_manager=None,
):
    """Build a GuestyProvider serving the account custom-fields endpoint.

    Uses a real ``GuestyTokenManager`` with a pre-seeded token cache, so
    the account identity behind the definitions cache is the production
    one rather than a stub, and no token request is made.

    Args:
        handler: Callable taking an httpx.Request and returning a Response.
        credential_id: Credential row ID backing the token manager.
        client_id: Guesty OAuth client ID currently configured.
        token_manager: Optional pre-built token manager to use instead.

    Returns:
        Configured GuestyProvider.
    """
    if token_manager is None:
        token_manager = GuestyTokenManager(
            client_id=client_id,
            client_secret="secret",
            credential_repo=AsyncMock(),
            credential_id=credential_id,
        )
        token_manager._cached_token = "tok"
        token_manager._cached_expires_at = datetime.now(UTC) + timedelta(hours=1)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=GUESTY_BASE_URL,
    )
    return GuestyProvider(token_manager=token_manager, http_client=client)


# Guesty populates ``key`` with the label the account owner typed and
# ``displayName`` with the slug derived from it (verified against the live
# API by our Home Assistant Guesty integration, commit ``62f58cc``).
SAMPLE_DEFINITIONS = [
    {
        "fieldId": "6a5a9f38050dfd00134fe27e",
        "key": "Test door lock code",
        "displayName": "test_door_lock_code",
        "object": "reservation",
        "type": "number",
    },
    {
        "fieldId": "6a5a9f38050dfd00134fe27f",
        "key": "Wifi password",
        "displayName": "listing_wifi_password",
        "object": "listing",
        "type": "string",
    },
]


class TestCustomFieldDefinitions:
    """Tests for GuestyProvider.get_custom_field_definitions."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """Isolate the process-wide definitions cache between tests."""
        clear_custom_field_definitions_cache()
        yield
        clear_custom_field_definitions_cache()

    @pytest.mark.asyncio
    async def test_key_wins_and_is_verbatim(self):
        """Test the human-readable ``key`` is used unchanged.

        ``key`` holds the label the account owner typed and
        ``displayName`` its slug, so preferring ``displayName`` would
        lose the owner's casing (``Test Door Lock Code``).
        """

        def handler(request):
            """Serve the account definitions.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            assert request.url.path == "/v1/accounts/me/custom-fields"
            assert not request.url.params
            return httpx.Response(200, json=SAMPLE_DEFINITIONS)

        provider = _definitions_provider(handler)
        labels = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert labels["6a5a9f38050dfd00134fe27e"] == "Test door lock code"

    @pytest.mark.asyncio
    async def test_listing_definitions_are_filtered_out(self):
        """Test only ``object == "reservation"`` definitions are kept."""

        def handler(request):
            """Serve the account definitions.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(200, json=SAMPLE_DEFINITIONS)

        provider = _definitions_provider(handler)
        labels = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert list(labels) == ["6a5a9f38050dfd00134fe27e"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", [None, "", "   "])
    async def test_display_name_is_used_when_key_is_absent(self, key):
        """Test a missing ``key`` falls back to the ``displayName`` slug."""
        definition = {
            "fieldId": "6a5a9f38050dfd00134fe27e",
            "displayName": "test_door_lock_code",
            "object": "reservation",
        }
        if key is not None:
            definition["key"] = key

        def handler(request):
            """Serve a definition lacking a usable key.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(200, json=[definition])

        provider = _definitions_provider(handler)
        labels = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert labels == {"6a5a9f38050dfd00134fe27e": "Test Door Lock Code"}

    @pytest.mark.asyncio
    async def test_both_scoped_definitions_are_kept(self):
        """Test ``object == "both"`` definitions still produce a label.

        A field scoped to listings *and* reservations carries a label a
        reservation value needs; dropping it left the field rendering as
        a raw ObjectId (issue #127).
        """
        definitions = [
            {
                "fieldId": "6a5a9f38050dfd00134fe280",
                "key": "Shared door code",
                "displayName": "shared_door_code",
                "object": "both",
            },
            {
                "fieldId": "6a5a9f38050dfd00134fe281",
                "key": "Listing only",
                "displayName": "listing_only",
                "object": "listing",
            },
        ]

        def handler(request):
            """Serve a both-scoped and a listing-scoped definition.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(200, json=definitions)

        provider = _definitions_provider(handler)
        labels = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert labels == {"6a5a9f38050dfd00134fe280": "Shared door code"}

    @pytest.mark.asyncio
    async def test_definitions_are_cached_across_calls(self):
        """Test repeated syncs do not re-request the definitions."""
        calls = []

        def handler(request):
            """Count every definitions request.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request.url.path)
            return httpx.Response(200, json=SAMPLE_DEFINITIONS)

        provider = _definitions_provider(handler)
        for _ in range(3):
            labels = await provider.get_custom_field_definitions()
            assert labels["6a5a9f38050dfd00134fe27e"] == "Test door lock code"
        await provider.aclose()

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_cache_is_shared_by_a_new_provider_instance(self):
        """Test a provider rebuilt for the next sync reuses the cache.

        Providers are constructed per sync run, so an instance-local
        cache would still cost one call per sync.
        """
        calls = []

        def handler(request):
            """Count every definitions request.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request.url.path)
            return httpx.Response(200, json=SAMPLE_DEFINITIONS)

        for _ in range(3):
            provider = _definitions_provider(handler)
            await provider.get_custom_field_definitions()
            await provider.aclose()

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_expired_cache_is_refetched(self):
        """Test definitions are re-requested once the TTL lapses."""
        calls = []

        def handler(request):
            """Count every definitions request.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request.url.path)
            return httpx.Response(200, json=SAMPLE_DEFINITIONS)

        provider = _definitions_provider(handler)
        await provider.get_custom_field_definitions()
        key, (_, labels) = next(iter(service._definitions_cache.items()))
        service._definitions_cache[key] = (
            datetime.now(UTC) - timedelta(seconds=1),
            labels,
        )
        await provider.get_custom_field_definitions()
        await provider.aclose()

        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_forbidden_degrades_without_raising(self, caplog):
        """Test a 403 (no custom-field permission) never breaks a sync."""

        def handler(request):
            """Deny access to the definitions endpoint.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(403, json={"error": "forbidden"})

        provider = _definitions_provider(handler)
        with caplog.at_level(logging.WARNING):
            labels = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert labels == {}
        assert "custom-field names" in caplog.text

    @pytest.mark.asyncio
    async def test_failure_is_cached_so_it_is_not_retried_every_sync(self):
        """Test a denied lookup does not re-request on the next sync."""
        calls = []

        def handler(request):
            """Deny access and count the attempt.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request.url.path)
            return httpx.Response(403, json={"error": "forbidden"})

        for _ in range(3):
            provider = _definitions_provider(handler)
            assert await provider.get_custom_field_definitions() == {}
            await provider.aclose()

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_server_error_degrades_without_raising(self):
        """Test a generic API failure yields an empty mapping."""

        def handler(request):
            """Fail the definitions request.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(500, text="boom")

        provider = _definitions_provider(handler)
        labels = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert labels == {}

    @pytest.mark.asyncio
    async def test_malformed_payload_degrades_without_raising(self, caplog):
        """Test a non-list body is rejected rather than exploding."""

        def handler(request):
            """Serve a body of an unexpected shape.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(200, json={"unexpected": "shape"})

        provider = _definitions_provider(handler)
        with caplog.at_level(logging.WARNING):
            labels = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert labels == {}
        assert "custom-field" in caplog.text

    @pytest.mark.asyncio
    async def test_invalid_json_degrades_without_raising(self):
        """Test an undecodable body is tolerated."""

        def handler(request):
            """Serve a body that is not JSON.

            Args:
                request: Outgoing request.

            Returns:
                Mocked response.
            """
            return httpx.Response(200, text="<html>nope</html>")

        provider = _definitions_provider(handler)
        labels = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert labels == {}

    @pytest.mark.asyncio
    async def test_entries_missing_required_values_are_skipped(self):
        """Test unusable definitions do not poison the mapping."""
        payload = [
            "not-a-dict",
            {"key": "no_field_id", "object": "reservation"},
            {"fieldId": "", "key": "blank_id", "object": "reservation"},
            {"fieldId": "abc", "object": "reservation"},
            {"fieldId": "def", "displayName": 7, "object": "reservation"},
            {"fieldId": "ghi", "key": "kept_field", "object": "reservation"},
        ]

        def handler(request):
            """Serve a mixed-quality definitions list.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(200, json=payload)

        provider = _definitions_provider(handler)
        labels = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert labels == {"ghi": "Kept Field"}

    @pytest.mark.asyncio
    async def test_wrapped_results_payload_is_accepted(self):
        """Test a ``results``-wrapped body is unwrapped."""

        def handler(request):
            """Serve definitions wrapped in a results envelope.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(200, json={"results": SAMPLE_DEFINITIONS})

        provider = _definitions_provider(handler)
        labels = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert labels == {"6a5a9f38050dfd00134fe27e": "Test door lock code"}

    @pytest.mark.asyncio
    async def test_cache_is_scoped_per_credential(self):
        """Test two Guesty accounts do not share one definition set."""
        calls = []

        def handler(request):
            """Count every definitions request.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request.url.path)
            return httpx.Response(200, json=SAMPLE_DEFINITIONS)

        for credential_id in (1, 2):
            provider = _definitions_provider(handler, credential_id=credential_id)
            await provider.get_custom_field_definitions()
            await provider.aclose()

        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_repointing_the_client_id_invalidates_the_cache(self):
        """Test switching Guesty accounts does not reuse stale labels.

        ``configure_oauth`` overwrites client_id/client_secret on the
        existing credential row, so the row ID is stable across an
        account switch and cannot scope the cache on its own.
        """
        responses = [
            [
                {
                    "fieldId": "ACCOUNT_A_FIELD",
                    "displayName": "Account A field",
                    "object": "reservation",
                }
            ],
            [
                {
                    "fieldId": "ACCOUNT_B_FIELD",
                    "displayName": "Account B field",
                    "object": "reservation",
                }
            ],
        ]
        calls = []

        def handler(request):
            """Serve the next account's definitions.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(200, json=responses[len(calls) - 1])

        def counting_handler(request):
            """Count the request, then serve it.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request.url.path)
            return handler(request)

        first = _definitions_provider(
            counting_handler, credential_id=7, client_id="client-a"
        )
        assert await first.get_custom_field_definitions() == {
            "ACCOUNT_A_FIELD": "Account A field"
        }
        await first.aclose()

        # Same credential row, new Guesty client credentials.
        second = _definitions_provider(
            counting_handler, credential_id=7, client_id="client-b"
        )
        labels = await second.get_custom_field_definitions()
        await second.aclose()

        assert labels == {"ACCOUNT_B_FIELD": "Account B field"}
        assert len(calls) == 2

    def test_account_key_hides_the_client_credentials(self):
        """Test the cache key never carries the client ID or secret."""
        manager = GuestyTokenManager(
            client_id="super-secret-client",
            client_secret="super-secret-value",
            credential_repo=AsyncMock(),
            credential_id=7,
        )

        account_key = manager.account_key

        assert "super-secret-client" not in account_key
        assert "super-secret-value" not in account_key
        assert re.fullmatch(r"[0-9a-f]{16}", account_key)
        assert manager.account_key == account_key

    @pytest.mark.asyncio
    async def test_unknown_account_identity_is_not_cached(self):
        """Test an unidentifiable account never shares a cache entry.

        Collapsing to one shared key would serve one account's labels to
        every other account, so caching is skipped instead.
        """
        calls = []

        def handler(request):
            """Count every definitions request.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request.url.path)
            return httpx.Response(200, json=SAMPLE_DEFINITIONS)

        stub = AsyncMock()
        stub.get_token = AsyncMock(return_value="tok")
        stub.account_key = None

        for _ in range(2):
            provider = _definitions_provider(handler, token_manager=stub)
            labels = await provider.get_custom_field_definitions()
            await provider.aclose()
            assert labels["6a5a9f38050dfd00134fe27e"] == "Test door lock code"

        assert len(calls) == 2
        assert service._definitions_cache == {}

    @pytest.mark.asyncio
    async def test_empty_definition_list_is_a_cached_success(self):
        """Test an account with no custom fields is not retried hourly."""
        calls = []

        def handler(request):
            """Serve an empty, valid definitions list.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request.url.path)
            return httpx.Response(200, json=[])

        provider = _definitions_provider(handler)
        assert await provider.get_custom_field_definitions() == {}
        expires_at, _ = next(iter(service._definitions_cache.values()))
        await provider.aclose()

        assert len(calls) == 1
        # Cached for the success TTL, not the short failure TTL.
        assert expires_at - datetime.now(UTC) > (
            service.CUSTOM_FIELD_DEFINITIONS_FAILURE_TTL
        )

    @pytest.mark.asyncio
    async def test_malformed_payload_uses_the_failure_cache_ttl(self):
        """Test a transient bad shape does not block labels for a day."""
        calls = []

        def handler(request):
            """Serve a body of an unexpected shape.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            calls.append(request.url.path)
            return httpx.Response(200, json={"unexpected": "shape"})

        provider = _definitions_provider(handler)
        assert await provider.get_custom_field_definitions() == {}
        expires_at, _ = next(iter(service._definitions_cache.values()))
        await provider.aclose()

        assert len(calls) == 1
        ttl = expires_at - datetime.now(UTC)
        assert ttl <= service.CUSTOM_FIELD_DEFINITIONS_FAILURE_TTL
        assert ttl < service.CUSTOM_FIELD_DEFINITIONS_TTL

    @pytest.mark.asyncio
    async def test_malformed_payload_recovers_after_the_failure_ttl(self):
        """Test labels resolve again once the endpoint recovers."""
        payloads = [{"unexpected": "shape"}, SAMPLE_DEFINITIONS]

        def handler(request):
            """Serve a malformed body, then a healthy one.

            Args:
                request: Outgoing request.

            Returns:
                Mocked JSON response.
            """
            return httpx.Response(200, json=payloads.pop(0) if payloads else [])

        provider = _definitions_provider(handler)
        assert await provider.get_custom_field_definitions() == {}

        key, (_, labels) = next(iter(service._definitions_cache.items()))
        service._definitions_cache[key] = (
            datetime.now(UTC) - timedelta(seconds=1),
            labels,
        )
        recovered = await provider.get_custom_field_definitions()
        await provider.aclose()

        assert recovered == {"6a5a9f38050dfd00134fe27e": "Test door lock code"}


class TestBaseProviderDefinitions:
    """Providers without custom-field definitions degrade to nothing."""

    @pytest.mark.asyncio
    async def test_default_implementation_returns_empty_mapping(self):
        """Test the base implementation costs nothing and names nothing."""
        from src.providers.cloudbeds.provider import CloudbedsProvider

        provider = CloudbedsProvider(access_token="tok")

        assert await provider.get_custom_field_definitions() == {}
