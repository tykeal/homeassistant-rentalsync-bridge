# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Map Guesty Reservations V3 search items to ``PMSReservation`` DTOs."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import replace
from datetime import date
from typing import Any

from src.providers.base import (
    RECOGNISED_NON_EXPORT_STATUSES,
    InlineCustomFields,
    PMSOccupancyInterval,
    PMSProviderError,
    PMSReservation,
)
from src.providers.guesty.legacy_reservations import (
    _EXCLUDED_STATUSES,
    _STATUS_MAP,
    _parse_date,
)

logger = logging.getLogger(__name__)

# Reservation-level keys carried into ``custom_data`` unchanged (FR-034).
_CUSTOM_KEYS = ("source", "confirmationCode")

# Stay-segment keys that feed the change key (research R9).
_SEGMENT_KEYS = ("listingId", "parentListingId", "checkIn", "checkOut", "guestsCount")


def reservation_id(item: dict[str, Any]) -> str:
    """Return a search item's ``reservationId``.

    Args:
        item: Raw search item.

    Returns:
        The reservation ID.

    Raises:
        PMSProviderError: If the item carries no usable ID.
    """
    value = item.get("reservationId")
    if not isinstance(value, str) or not value:
        msg = (
            "Guesty Reservations V3 returned a search item without a "
            "reservationId; refusing to return an unattributable snapshot"
        )
        raise PMSProviderError(msg)
    return value


def stay_segments(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a search item's validated ``stay[]`` segments.

    A reservation whose stay names no listing cannot be attributed, and
    guessing an owner is unsafe either way (FR-020), so the fetch aborts.

    Args:
        item: Raw search item.

    Returns:
        The segments, each carrying a string ``listingId``.

    Raises:
        PMSProviderError: If ``stay`` is missing or empty, or any segment
            has no usable ``listingId``.
    """
    stay = item.get("stay")
    if (
        isinstance(stay, list)
        and stay
        and all(
            isinstance(segment, dict)
            and isinstance(segment.get("listingId"), str)
            and segment["listingId"]
            for segment in stay
        )
    ):
        return stay
    msg = (
        f"Guesty reservation {item.get('reservationId') or '<unknown>'} "
        f"carries no usable stay listing reference; refusing to guess its "
        f"owner (please report this reservation ID)"
    )
    raise PMSProviderError(msg)


def segment_owned(segment: dict[str, Any], scope_ids: frozenset[str]) -> bool:
    """Report whether a listing owns one stay segment (research R6).

    A segment is owned through its own listing or its parent, so a new
    sub-unit of a tracked multi-unit listing is owned before the listing
    document lists it.  A tracked child's scope never holds its parent,
    so a sibling's segment is not owned.

    Args:
        segment: A validated stay segment.
        scope_ids: Listing IDs owned by the listing being synced.

    Returns:
        True when the segment's listing or parent is in *scope_ids*.
    """
    return (
        segment["listingId"] in scope_ids or segment.get("parentListingId") in scope_ids
    )


def owned_by(item: dict[str, Any], scope_ids: frozenset[str]) -> bool:
    """Report whether a listing owns any segment of a reservation.

    Every segment counts (research R6): a stay moved in from another
    listing belongs to each tracked listing it occupies.

    Args:
        item: Raw search item.
        scope_ids: Listing IDs owned by the listing being synced.

    Returns:
        True when any stay segment is owned (see ``segment_owned``).

    Raises:
        PMSProviderError: If the stay is unusable (see ``stay_segments``).
    """
    return any(segment_owned(s, scope_ids) for s in stay_segments(item))


def change_key(item: dict[str, Any]) -> str:
    """Return a content hash of the fields the sync consumes (research R9).

    Args:
        item: Raw search item.

    Returns:
        Hex SHA-256 of the canonical JSON of those fields.
    """
    stay = item.get("stay") if isinstance(item.get("stay"), list) else []
    fields = item.get("customFields")
    if isinstance(fields, list):
        fields = sorted(
            fields,
            key=lambda f: str(f.get("fieldId")) if isinstance(f, dict) else str(f),
        )
    material = {
        "reservationId": item.get("reservationId"),
        "status": item.get("status"),
        "guestId": item.get("guestId"),
        "confirmationCode": item.get("confirmationCode"),
        "source": item.get("source"),
        "checkIn": item.get("checkIn"),
        "checkOut": item.get("checkOut"),
        "stay": [
            {key: segment.get(key) for key in _SEGMENT_KEYS}
            if isinstance(segment, dict)
            else segment
            for segment in stay or []
        ],
        "customFields": fields,
        "notes": item.get("notes"),
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def inline_custom_fields(item: dict[str, Any]) -> InlineCustomFields:
    """Parse a search item's ``customFields`` (research R8).

    Args:
        item: Raw search item.

    Returns:
        ``present`` with ``{fieldId: value}``, ``absent`` when the key is
        missing, or ``malformed`` when it is not a list of well-formed
        ``{fieldId, value}`` objects.
    """
    if "customFields" not in item:
        return InlineCustomFields("absent")
    fields = item["customFields"]
    if not isinstance(fields, list) or not all(
        isinstance(f, dict) and isinstance(f.get("fieldId"), str) and "value" in f
        for f in fields
    ):
        return InlineCustomFields("malformed")
    return InlineCustomFields("present", {f["fieldId"]: f["value"] for f in fields})


def _status(item: dict[str, Any], rid: str) -> str | None:
    """Normalise a search item's status like the legacy source.

    Args:
        item: Raw search item.
        rid: Its reservation ID, for messages.

    Returns:
        The normalised status, the raw status for an unrecognised one
        (withheld from export), or None for an excluded status.

    Raises:
        PMSProviderError: If the item carries no ``status`` key.
    """
    if "status" not in item:
        msg = (
            f"Guesty reservation {rid} carries no 'status' field; refusing "
            f"to classify reservations without it — the existing calendar "
            f"data is kept instead of being replaced by an unsafe snapshot"
        )
        raise PMSProviderError(msg)
    raw = str(item.get("status") or "").lower()
    if raw in _EXCLUDED_STATUSES:
        return None
    status = _STATUS_MAP.get(raw)
    if status is not None:
        return status
    if raw in RECOGNISED_NON_EXPORT_STATUSES:
        logger.debug(
            "Guesty reservation %s reports status %r, a known non-stay "
            "state; booking withheld from calendar export as expected",
            rid,
            raw,
        )
    else:
        logger.warning(
            "Guesty reservation %s reports status %r, which is present but "
            "unrecognised; booking withheld from calendar export",
            rid,
            raw,
        )
    return raw


def _local_date(value: Any) -> date | None:
    """Parse a listing-localized ``YYYY-MM-DD`` date, or None."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _guests(value: Any) -> int | None:
    """Return a segment's ``guestsCount`` when it is an integer."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _adjacent(previous: dict[str, Any], segment: dict[str, Any]) -> bool:
    """Report whether a segment continues the previous one in its room."""
    if previous["listingId"] != segment["listingId"]:
        return False
    check_out, check_in = previous.get("checkOut"), segment.get("checkIn")
    if not check_out or not check_in:
        return False
    return _parse_date(check_out) == _parse_date(check_in)


def _merge(
    interval: PMSOccupancyInterval, segment: dict[str, Any]
) -> PMSOccupancyInterval:
    """Extend an interval by the adjacent same-room segment after it."""
    check_out_local = _local_date(segment.get("checkOutDateLocalized"))
    local_dates = (
        (interval.local_dates[0], check_out_local)
        if interval.local_dates and check_out_local
        else None
    )
    counts = [
        n
        for n in (interval.guests_count, _guests(segment.get("guestsCount")))
        if n is not None
    ]
    return replace(
        interval,
        check_out=_parse_date(segment.get("checkOut")),
        local_dates=local_dates,
        guests_count=max(counts) if counts else None,
    )


def _intervals(
    segments: list[dict[str, Any]],
    scope_ids: frozenset[str],
    *,
    with_instants: bool = True,
) -> tuple[PMSOccupancyInterval, ...]:
    """Build the occupancy intervals of the segments a listing owns.

    Consecutive owned segments in one room whose check-out instant is the
    next check-in are one interval; a later return to a room is another,
    and ordinals count those per room (research R6).

    Args:
        segments: Validated stay segments.
        scope_ids: Listing IDs owned by the listing being synced.
        with_instants: False for by-IDs segments, which carry only local
            dates; their intervals then have no UTC instants and never
            merge (an equal same-room boundary date is ambiguous there).

    Returns:
        The owned intervals in stay order.
    """
    seen: Counter[str] = Counter()
    intervals: list[PMSOccupancyInterval] = []
    previous: dict[str, Any] | None = None
    for segment in segments:
        owned = segment_owned(segment, scope_ids)
        if (
            owned
            and with_instants
            and previous is not None
            and _adjacent(previous, segment)
        ):
            intervals[-1] = _merge(intervals[-1], segment)
            continue
        previous = segment if owned else None
        if not owned:
            continue
        room = segment["listingId"]
        seen[room] += 1
        check_in_local = _local_date(segment.get("checkInDateLocalized"))
        check_out_local = _local_date(segment.get("checkOutDateLocalized"))
        intervals.append(
            PMSOccupancyInterval(
                room_id=room,
                ordinal=seen[room],
                check_in=_parse_date(segment.get("checkIn")) if with_instants else None,
                check_out=_parse_date(segment.get("checkOut"))
                if with_instants
                else None,
                local_dates=(check_in_local, check_out_local)
                if check_in_local and check_out_local
                else None,
                guests_count=_guests(segment.get("guestsCount")),
            )
        )
    return tuple(intervals)


def map_search_item(
    item: dict[str, Any],
    listing_pms_id: str,
    scope_ids: frozenset[str],
) -> PMSReservation | None:
    """Convert one owned search item to a ``PMSReservation``.

    Args:
        item: Raw search item owned by the listing (see ``owned_by``).
        listing_pms_id: Owning listing ID.
        scope_ids: Listing IDs owned by that listing.

    Returns:
        The DTO, or None for an excluded status or a stay with no owned
        segment.  A stay that also occupies other listings spans only its
        owned intervals.

    Raises:
        PMSProviderError: If the item has no ID, status or usable stay, or
            its dates cannot be parsed (an unmapped reservation would
            otherwise be retired as absent).
    """
    rid = reservation_id(item)
    segments = stay_segments(item)
    status = _status(item, rid)
    if status is None:
        return None
    intervals = _intervals(segments, scope_ids)
    if not intervals:
        return None
    if all(segment_owned(s, scope_ids) for s in segments):
        check_in = _parse_date(item.get("checkIn") or segments[0].get("checkIn"))
        check_out = _parse_date(item.get("checkOut") or segments[-1].get("checkOut"))
    else:
        # A stay that also occupies other listings spans only its owned nights.
        check_in = min(i.check_in for i in intervals if i.check_in)
        check_out = max(i.check_out for i in intervals if i.check_out)
    guest_id = item.get("guestId")
    return PMSReservation(
        pms_booking_id=rid,
        listing_pms_id=listing_pms_id,
        guest_name=None,
        guest_id=str(guest_id) if guest_id else None,
        check_in=check_in,
        check_out=check_out,
        status=status,
        room_ids=tuple(dict.fromkeys(i.room_id for i in intervals)),
        custom_data={k: item[k] for k in _CUSTOM_KEYS if item.get(k) is not None},
        intervals=intervals,
        change_key=change_key(item),
        inline_custom_fields=inline_custom_fields(item),
    )
