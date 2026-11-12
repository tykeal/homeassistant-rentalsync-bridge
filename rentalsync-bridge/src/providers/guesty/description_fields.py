# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty description fields under Reservations V3 (research R10)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from types import MappingProxyType
from typing import Any

from src.providers.base import PMSOccupancyInterval

# Reservation-level keys carried into ``custom_data`` unchanged (FR-034).
_CUSTOM_KEYS = ("source", "confirmationCode")

# ``notes`` parts joined into the description, in order.  ``cleaning`` and
# ``keyCode`` are deliberately never exported.
_NOTE_KEYS = ("other", "guest", "specialRequests")

_PAYMENT_TOTALS_REASON = (
    "Guesty Reservations V3 no longer includes payment totals on reservations"
)

# Description fields legacy supplied and V3 cannot (FR-036): they are
# flagged, never re-sourced, and their configuration is never deleted.
RETIRED_DESCRIPTION_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "money_totalPaid": _PAYMENT_TOTALS_REASON,
        "money_balanceDue": _PAYMENT_TOTALS_REASON,
        "money_currency": _PAYMENT_TOTALS_REASON,
    }
)


def retired_fields_for(pms_type: str, reservations_api: str) -> Mapping[str, str]:
    """Return the description fields retired for a PMS configuration.

    A static lookup, so the admin API needs neither a provider nor a
    network call.  Only Guesty read through Reservations V3 retires any.

    Args:
        pms_type: The credential's PMS type.
        reservations_api: The configured Guesty reservations API.

    Returns:
        Mapping of retired field key to a user-facing reason; empty when
        nothing is retired.
    """
    if pms_type == "guesty" and reservations_api == "v3":
        return RETIRED_DESCRIPTION_FIELDS
    return MappingProxyType({})


def _local_date(value: Any) -> date | None:
    """Parse a listing-localized ``YYYY-MM-DD`` date, or None."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _nights(item: dict[str, Any], segments: Sequence[dict[str, Any]]) -> int | None:
    """Return the whole reservation's nights from its localized dates."""
    check_in = _local_date(
        item.get("checkInDateLocalized") or segments[0].get("checkInDateLocalized")
    )
    check_out = _local_date(
        item.get("checkOutDateLocalized") or segments[-1].get("checkOutDateLocalized")
    )
    if check_in is None or check_out is None:
        return None
    nights = (check_out - check_in).days
    return nights if nights > 0 else None


def _notes(item: dict[str, Any]) -> str | None:
    """Join the exportable notes, or None when there is no note text."""
    notes = item.get("notes")
    if not isinstance(notes, dict):
        return None
    parts = [notes.get(key) for key in _NOTE_KEYS]
    joined = "; ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
    return joined or None


def description_fields(
    item: dict[str, Any],
    segments: Sequence[dict[str, Any]],
    intervals: Sequence[PMSOccupancyInterval],
) -> dict[str, Any]:
    """Build a V3 search item's ``custom_data`` with the legacy keys.

    Keys follow the legacy order.  ``nightsCount`` spans the whole
    reservation, as legacy did; ``guestsCount`` is the largest owned
    interval's count, which each interval booking then narrows to its
    own.  The payment totals are retired: V3 does not carry them.

    Args:
        item: Raw search item.
        segments: Its validated, non-empty ``stay[]`` segments.
        intervals: The intervals the owning listing occupies.

    Returns:
        The reservation's description fields.
    """
    custom: dict[str, Any] = {
        k: item[k] for k in _CUSTOM_KEYS if item.get(k) is not None
    }
    nights = _nights(item, segments)
    if nights is not None:
        custom["nightsCount"] = nights
    counts = [i.guests_count for i in intervals if i.guests_count is not None]
    if counts:
        custom["guestsCount"] = max(counts)
    notes = _notes(item)
    if notes is not None:
        custom["notes"] = notes
    return custom
