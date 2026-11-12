# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""The Reservations V3 by-IDs lookup behind confirmed absence (research R5)."""

from __future__ import annotations

import itertools
import logging
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

from src.providers.base import (
    AbsenceResult,
    AbsenceVerdict,
    PMSProviderError,
    PMSReservation,
)
from src.providers.guesty.legacy_reservations import _EXCLUDED_STATUSES
from src.providers.guesty.sources import GuestyHTTPError
from src.providers.guesty.v3_mapping import (
    _intervals,
    _local_date,
    _status,
    segment_owned,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.providers.guesty.sources import RequestFn

logger = logging.getLogger(__name__)

BY_IDS_PATH = "/v1/reservations-v3"

# Candidates per call; one more slot carries the control (10 IDs maximum).
MAX_CANDIDATES_PER_CALL = 9

_CANCELLED = frozenset({"canceled", "cancelled"})
_HELD = frozenset({"alteration"})

_UNCONFIRMED = AbsenceVerdict.UNCONFIRMED


def _verdict(verdict: AbsenceVerdict, detail: str) -> AbsenceResult:
    """Return a result without a reservation."""
    return AbsenceResult(verdict, detail=detail)


def _normalise_segment(segment: Any) -> dict[str, Any] | None:
    """Return one by-IDs ``stay[]`` segment in the search shape, or None."""
    if not isinstance(segment, dict):
        return None
    unit_id, unit_type_id = segment.get("unitId"), segment.get("unitTypeId")
    unit_id = unit_id if isinstance(unit_id, str) and unit_id else None
    unit_type_id = (
        unit_type_id if isinstance(unit_type_id, str) and unit_type_id else None
    )
    listing_id = unit_id or unit_type_id
    if listing_id is None:
        return None
    normalised: dict[str, Any] = {"listingId": listing_id}
    if unit_id and unit_type_id and unit_type_id != unit_id:
        normalised["parentListingId"] = unit_type_id
    for key in ("checkInDateLocalized", "checkOutDateLocalized", "guestsCount"):
        if key in segment:
            normalised[key] = segment[key]
    return normalised


def normalise_by_ids_item(raw: Any) -> dict[str, Any] | None:
    """Rewrite a by-IDs item in the search-item shape (research R5).

    ``_id`` becomes ``reservationId``; each segment's ``unitId ??
    unitTypeId`` becomes ``listingId``, with ``unitTypeId`` as
    ``parentListingId`` only when the unit differs from it.  Only the
    listing-localized dates exist, so no ``checkIn``/``checkOut`` keys.

    Args:
        raw: One item of the by-IDs response.

    Returns:
        The search-shaped item, or None when it has no usable ID or stay.
    """
    if not isinstance(raw, dict):
        return None
    rid = raw.get("_id")
    stay = raw.get("stay")
    if not isinstance(rid, str) or not rid or not isinstance(stay, list) or not stay:
        return None
    segments = [_normalise_segment(segment) for segment in stay]
    if any(segment is None for segment in segments):
        return None
    item: dict[str, Any] = {"reservationId": rid, "stay": segments}
    for key in ("status", "guestId"):
        if key in raw:
            item[key] = raw[key]
    return item


def ambiguous_rooms(item: dict[str, Any]) -> set[str]:
    """Return rooms whose consecutive segments meet on one local date.

    Without UTC instants, two same-room segments whose check-out and
    check-in fall on the same local date may be one interval or two, so
    their ``#n`` identities cannot be rebuilt.

    Args:
        item: A normalised by-IDs item.

    Returns:
        The listing IDs of the ambiguous rooms.
    """
    stay = item["stay"]
    return {
        current["listingId"]
        for previous, current in itertools.pairwise(stay)
        if previous["listingId"] == current["listingId"]
        and previous.get("checkOutDateLocalized") == current.get("checkInDateLocalized")
    }


def _stay_dates(item: dict[str, Any]) -> tuple[date, date] | None:
    """Return the whole stay's first local check-in and last check-out."""
    starts, ends = [], []
    for segment in item["stay"]:
        start = _local_date(segment.get("checkInDateLocalized"))
        end = _local_date(segment.get("checkOutDateLocalized"))
        if start is None or end is None:
            return None
        starts.append(start)
        ends.append(end)
    return min(starts), max(ends)


def map_by_ids_item(
    item: dict[str, Any],
    listing_pms_id: str,
    scope_ids: frozenset[str],
) -> PMSReservation | None:
    """Convert a normalised by-IDs item to a ``PMSReservation``.

    Intervals come from the same R6 code as a search item, so booking IDs
    are byte-identical; they carry local dates only.  The reservation's
    own check-in and check-out are UTC midnight of the stay's first and
    last local dates: the sync re-anchors each stored row from its
    interval and never stores these.

    Args:
        item: A normalised by-IDs item (see ``normalise_by_ids_item``).
        listing_pms_id: Listing the reservation is attributed to.
        scope_ids: Listing IDs owned by that listing.

    Returns:
        The DTO, or None for an excluded status or unusable dates.

    Raises:
        PMSProviderError: If the item carries no ``status`` key.
    """
    rid = item["reservationId"]
    status = _status(item, rid)
    dates = _stay_dates(item)
    if status is None or dates is None:
        return None
    intervals = _intervals(item["stay"], scope_ids, with_instants=False)
    guest_id = item.get("guestId")
    return PMSReservation(
        pms_booking_id=rid,
        listing_pms_id=listing_pms_id,
        guest_name=None,
        guest_id=str(guest_id) if guest_id else None,
        check_in=datetime.combine(dates[0], time(), tzinfo=UTC),
        check_out=datetime.combine(dates[1], time(), tzinfo=UTC),
        status=status,
        room_ids=tuple(dict.fromkeys(i.room_id for i in intervals)),
        custom_data={},
        intervals=intervals,
    )


class AbsenceContext:
    """What one listing's absence check is judged against (research R5)."""

    def __init__(
        self,
        *,
        listing_pms_id: str,
        scope_ids: frozenset[str],
        filter_ids: frozenset[str],
        searched_ids: frozenset[str],
        window: tuple[date, date],
    ) -> None:
        """Initialize the context.

        Args:
            listing_pms_id: Listing being synced.
            scope_ids: Listing IDs that listing owns.
            filter_ids: Every listing ID the cycle's search filtered on.
            searched_ids: Reservation IDs the accepted search returned.
            window: The search's ``(D_start, D_end)`` localized bounds.
        """
        self.listing_pms_id = listing_pms_id
        self.scope_ids = scope_ids
        self.filter_ids = filter_ids
        self.searched_ids = searched_ids
        self.window = window

    def judge(self, item: dict[str, Any]) -> AbsenceResult:
        """Return the verdict for one returned, normalised candidate.

        Args:
            item: The candidate's normalised by-IDs item.

        Returns:
            The candidate's result.
        """
        return (
            self._by_status(item) or self._by_placement(item) or self._reattribute(item)
        )

    @staticmethod
    def _by_status(item: dict[str, Any]) -> AbsenceResult | None:
        """Settle a cancelled, excluded, held or status-less candidate."""
        if "status" not in item:
            return _verdict(_UNCONFIRMED, "returned without a status")
        raw = str(item.get("status") or "").lower()
        if raw in _CANCELLED or raw in _EXCLUDED_STATUSES:
            return _verdict(AbsenceVerdict.CONFIRMED_ABSENT, f"status {raw}")
        if raw in _HELD:
            return _verdict(_UNCONFIRMED, f"status {raw} (held)")
        return None

    def _by_placement(self, item: dict[str, Any]) -> AbsenceResult | None:
        """Settle a live candidate by its rooms, dates and first segment."""
        stay = item["stay"]
        if not any(segment_owned(s, self.scope_ids) for s in stay):
            return _verdict(AbsenceVerdict.CONFIRMED_ABSENT, "moved out of scope")
        dates = _stay_dates(item)
        if dates is None:
            return _verdict(_UNCONFIRMED, "unusable stay dates")
        if dates[1] < self.window[0] or dates[0] > self.window[1]:
            return _verdict(AbsenceVerdict.CONFIRMED_ABSENT, "moved out of window")
        rid = item["reservationId"]
        first = {stay[0]["listingId"], stay[0].get("parentListingId")}
        if first & self.filter_ids and rid not in self.searched_ids:
            return _verdict(
                AbsenceVerdict.INCONSISTENT,
                f"reservation {rid} is live in a searched listing but the "
                f"search did not return it; if this persists, set "
                f"guesty_reservations_api to legacy and report it",
            )
        return None

    def _reattribute(self, item: dict[str, Any]) -> AbsenceResult:
        """Rebuild a live candidate the search cannot see from its segments."""
        rid = item["reservationId"]
        owned = {
            s["listingId"] for s in item["stay"] if segment_owned(s, self.scope_ids)
        }
        ambiguous = ambiguous_rooms(item) & owned
        if ambiguous:
            logger.warning(
                "Guesty V3: reservation %s has same-room segments meeting on "
                "one local date in %s; keeping its stored bookings",
                rid,
                ", ".join(sorted(ambiguous)),
            )
            return _verdict(_UNCONFIRMED, "ambiguous same-room boundary")
        mapped = map_by_ids_item(item, self.listing_pms_id, self.scope_ids)
        if mapped is None or any(i.local_dates is None for i in mapped.intervals):
            return _verdict(_UNCONFIRMED, "unusable stay dates")
        return AbsenceResult(
            AbsenceVerdict.REATTRIBUTED,
            reservation=mapped,
            detail="re-attributed from the by-IDs lookup",
        )


def _validated(
    data: Any, requested: Sequence[str], listing_pms_id: str
) -> dict[str, dict[str, Any]] | None:
    """Validate a by-IDs response as a whole, logging why it is unusable.

    Args:
        data: The decoded response body.
        requested: The IDs asked for.
        listing_pms_id: Listing being synced, for messages.

    Returns:
        The normalised items by ID, or None when the batch proves nothing.
    """
    if not isinstance(data, list) or not all(
        isinstance(raw, dict) and isinstance(raw.get("_id"), str) for raw in data
    ):
        logger.warning(
            "Guesty V3 by-IDs lookup for listing %s returned a malformed "
            "response; keeping its absent reservations",
            listing_pms_id,
        )
        return None
    ids = [raw["_id"] for raw in data]
    if len(set(ids)) != len(ids) or not set(ids) <= set(requested):
        logger.error(
            "Guesty V3 by-IDs lookup for listing %s returned reservations "
            "that were not requested, or one twice; the reservationIds "
            "parameter was not applied, so its absent reservations are kept",
            listing_pms_id,
        )
        return None
    items = [normalise_by_ids_item(raw) for raw in data]
    if any(item is None for item in items):
        logger.warning(
            "Guesty V3 by-IDs lookup for listing %s returned an item without "
            "a usable stay; keeping its absent reservations",
            listing_pms_id,
        )
        return None
    return {item["reservationId"]: item for item in items if item is not None}


async def confirm_batch(
    request: RequestFn,
    context: AbsenceContext,
    candidates: Sequence[str],
    control: str | None,
) -> dict[str, AbsenceResult]:
    """Look up one batch of candidates (plus the control) by ID.

    Args:
        request: The provider's authenticated request method.
        context: What the verdicts are judged against.
        candidates: At most ``MAX_CANDIDATES_PER_CALL`` sorted IDs.
        control: A reservation the search returned, or None.

    Returns:
        One result per candidate.
    """
    requested = [*candidates, *([control] if control else [])]
    listing = context.listing_pms_id
    unconfirmed = dict.fromkeys(
        candidates, _verdict(_UNCONFIRMED, "by-IDs lookup unusable")
    )
    try:
        response = await request(
            "GET", BY_IDS_PATH, params={"reservationIds": requested}
        )
        data = response.json()
    except GuestyHTTPError as exc:
        level = logging.ERROR if exc.status_code == 400 else logging.WARNING  # noqa: PLR2004
        logger.log(
            level,
            "Guesty V3 by-IDs lookup for listing %s failed (HTTP %d); keeping "
            "its absent reservations",
            listing,
            exc.status_code,
        )
        return unconfirmed
    except (PMSProviderError, ValueError) as exc:
        logger.warning(
            "Guesty V3 by-IDs lookup for listing %s failed (%s); keeping its "
            "absent reservations",
            listing,
            type(exc).__name__,
        )
        return unconfirmed
    items = _validated(data, requested, listing)
    if items is None:
        return unconfirmed

    results: dict[str, AbsenceResult] = {}
    for rid in candidates:
        if rid in items:
            results[rid] = context.judge(items[rid])
        elif control is not None and control in items:
            results[rid] = _verdict(AbsenceVerdict.CONFIRMED_ABSENT, "deleted")
        else:
            logger.error(
                "Guesty V3 by-IDs lookup for listing %s omitted reservation "
                "%s without %s; keeping it, an omission proves nothing",
                listing,
                rid,
                "returning the control" if control else "a control to check",
            )
            results[rid] = _verdict(_UNCONFIRMED, "omitted, control unverified")
    return results
