# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty description fields under Reservations V3 (research R10)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from src.providers.base import PMSOccupancyInterval

# Reservation-level keys carried into ``custom_data`` unchanged (FR-034).
_CUSTOM_KEYS = ("source", "confirmationCode")

# ``notes`` parts joined into the description, in order.  ``cleaning`` and
# ``keyCode`` are deliberately never exported.
_NOTE_KEYS = ("other", "guest", "specialRequests")


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
