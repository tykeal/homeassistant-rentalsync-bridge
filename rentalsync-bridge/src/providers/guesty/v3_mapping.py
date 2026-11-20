# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Map Guesty Reservations V3 search items to ``PMSReservation`` DTOs."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from src.providers.base import (
    HOLD_STATUSES,
    RECOGNISED_NON_EXPORT_STATUSES,
    InlineCustomFields,
    PMSProviderError,
    PMSReservation,
)
from src.providers.guesty.description_fields import description_fields
from src.providers.guesty.legacy_reservations import (
    _CLASSIFIED_STATUSES,
    _EXCLUDED_STATUSES,
    _STATUS_MAP,
)
from src.providers.guesty.v3_intervals import occupancy_intervals, segment_owned
from src.providers.guesty.v3_stay_times import StayTimes, localized_stay

logger = logging.getLogger(__name__)

# The legacy status map plus V3's ``completed``, which is a finished stay
# exported and aged out like ``checked_out`` (research R7, FR-023).
V3_STATUS_MAP: dict[str, str] = {**_STATUS_MAP, "completed": "checked_out"}

# Statuses the health guard counts as understood (FR-025).  A hold status
# is transient, not evidence of a broken mapping.
V3_CLASSIFIED_STATUSES = _CLASSIFIED_STATUSES | HOLD_STATUSES

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


def owned_by(item: dict[str, Any], scope_ids: frozenset[str]) -> bool:
    """Report whether a listing owns any segment of a reservation.

    Every segment counts (research R6): a stay moved in from another
    listing belongs to each tracked listing it occupies.

    Args:
        item: Raw search item.
        scope_ids: Listing IDs owned by the listing being synced.

    Returns:
        True when any stay segment is owned (see ``v3_intervals.segment_owned``).

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
        The normalised status, the raw status for a hold status (see
        ``HOLD_STATUSES``) or an unrecognised one (withheld from export),
        or None for an excluded status.

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
    status = V3_STATUS_MAP.get(raw)
    if status is not None:
        return status
    if raw in HOLD_STATUSES:
        logger.debug(
            "Guesty reservation %s reports status %r, a transient edit "
            "state; its stored booking is held as last synced",
            rid,
            raw,
        )
    elif raw in RECOGNISED_NON_EXPORT_STATUSES:
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


def map_search_item(
    item: dict[str, Any],
    listing_pms_id: str,
    scope_ids: frozenset[str],
    *,
    stay_times: StayTimes | None = None,
) -> PMSReservation | None:
    """Convert one owned search item to a ``PMSReservation``.

    V3's ``checkIn``/``checkOut`` carry the listing's local wall-clock
    time labelled UTC, so instants come from the localized dates and the
    listing's configured times instead (see ``stay_instants``).

    Args:
        item: Raw search item owned by the listing (see ``owned_by``).
        listing_pms_id: Owning listing ID.
        scope_ids: Listing IDs owned by that listing.
        stay_times: Times of the listing and its rooms; None gives a
            date-only stay.

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
    times = stay_times or StayTimes()
    intervals = occupancy_intervals(segments, scope_ids, times=times)
    if not intervals:
        return None
    if all(segment_owned(s, scope_ids) for s in segments):
        _, (check_in, check_out) = localized_stay(
            (item, segments[0]),
            (item, segments[-1]),
            times.for_room(segments[0]["listingId"]),
            times.for_room(segments[-1]["listingId"]),
        )
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
        custom_data=description_fields(item, segments, intervals),
        intervals=intervals,
        change_key=change_key(item),
        inline_custom_fields=inline_custom_fields(item),
    )
