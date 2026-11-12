# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Map Guesty Reservations V3 search items to ``PMSReservation`` DTOs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.providers.base import (
    PMSProviderError,
    PMSReservation,
)
from src.providers.guesty.legacy_reservations import (
    _EXCLUDED_STATUSES,
    _STATUS_MAP,
    _parse_date,
)

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


def owned_by(item: dict[str, Any], scope_ids: frozenset[str]) -> bool:
    """Report whether a listing owns a reservation.

    A reservation belongs to the listing its first stay segment is in,
    matching how Guesty computes the legacy top-level ``listingId``.

    Args:
        item: Raw search item.
        scope_ids: Listing IDs owned by the listing being synced.

    Returns:
        True when the first segment's listing is in *scope_ids*.

    Raises:
        PMSProviderError: If the stay is unusable (see ``stay_segments``).
    """
    return stay_segments(item)[0]["listingId"] in scope_ids


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


def _status(item: dict[str, Any], rid: str) -> str | None:
    """Normalise a search item's status like the legacy source.

    Args:
        item: Raw search item.
        rid: Its reservation ID, for messages.

    Returns:
        The normalised (or raw, unrecognised) status, or None for an
        excluded status.

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
    return _STATUS_MAP.get(raw, raw)


def map_search_item(
    item: dict[str, Any],
    listing_pms_id: str,
    scope_ids: frozenset[str],  # noqa: ARG001
) -> PMSReservation | None:
    """Convert one owned search item to a ``PMSReservation``.

    Args:
        item: Raw search item owned by the listing (see ``owned_by``).
        listing_pms_id: Owning listing ID.
        scope_ids: Listing IDs owned by that listing.

    Returns:
        The DTO, or None for an excluded status.

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
    room = segments[0]["listingId"]
    guest_id = item.get("guestId")
    return PMSReservation(
        pms_booking_id=rid,
        listing_pms_id=listing_pms_id,
        guest_name=None,
        guest_id=str(guest_id) if guest_id else None,
        check_in=_parse_date(item.get("checkIn") or segments[0].get("checkIn")),
        check_out=_parse_date(item.get("checkOut") or segments[-1].get("checkOut")),
        status=status,
        room_ids=(room,),
        custom_data={k: item[k] for k in _CUSTOM_KEYS if item.get(k) is not None},
        change_key=change_key(item),
    )
