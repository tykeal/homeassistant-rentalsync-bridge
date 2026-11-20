# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Listing-local check-in/check-out instants for V3 stays (issue #212).

Reservations V3 labels the listing's local wall-clock check-in/check-out
time as UTC (``T16:00:00.000Z`` for a 16:00 America/New_York listing), so
its ``checkIn``/``checkOut`` offsets are not trusted.  An instant is built
from the stay's localized date, the listing's ``defaultCheckInTime`` /
``defaultCheckOutTime`` (``HH:mm``) and its IANA ``timezone`` instead.
Without a usable time or timezone the stay is date-only: midnight UTC on
the localized dates (see ``src.utils.stay_time``), exported all-day.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.providers.base import PMSProviderError

if TYPE_CHECKING:
    from collections.abc import Sequence

_HH_MM = re.compile(r"(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)")


@dataclass(frozen=True)
class ListingTimes:
    """A listing's raw configured times and timezone (None when absent)."""

    timezone: str | None = None
    check_in: str | None = None
    check_out: str | None = None

    def inherit(self, parent: ListingTimes) -> ListingTimes:
        """Fill each absent value from *parent*."""
        return ListingTimes(
            self.timezone if self.timezone is not None else parent.timezone,
            self.check_in if self.check_in is not None else parent.check_in,
            self.check_out if self.check_out is not None else parent.check_out,
        )

    def describe(self) -> str:
        """Render the values for DEBUG diagnostics."""
        return f"{self.check_in}/{self.check_out} {self.timezone}"


@dataclass(frozen=True)
class StayTimes:
    """The times of a synced listing and of the rooms it owns."""

    default: ListingTimes = field(default_factory=ListingTimes)
    rooms: dict[str, ListingTimes] = field(default_factory=dict)

    def for_room(self, room_id: str) -> ListingTimes:
        """Return a room's times, falling back to the listing's per value."""
        own = self.rooms.get(room_id)
        return own.inherit(self.default) if own is not None else self.default

    def with_rooms(self, rooms: dict[str, ListingTimes]) -> StayTimes:
        """Return a copy where *rooms* take precedence over known rooms."""
        return StayTimes(self.default, {**self.rooms, **rooms})


def _text(value: Any) -> str | None:
    """Return *value* when it is a string, else None (absent)."""
    return value if isinstance(value, str) else None


def listing_times(document: dict[str, Any]) -> ListingTimes:
    """Read a listing document's ``timezone`` and default times.

    Args:
        document: Raw listing document (or a ``childListings`` entry).

    Returns:
        The raw values; parsing is deferred so a bad value is date-only.
    """
    return ListingTimes(
        _text(document.get("timezone")),
        _text(document.get("defaultCheckInTime")),
        _text(document.get("defaultCheckOutTime")),
    )


def stay_times(document: dict[str, Any]) -> StayTimes:
    """Return the stay times of a listing document and its child entries.

    Args:
        document: Raw listing document from ``GET /v1/listings/{id}``.

    Returns:
        The listing's own times plus one entry per ``childListings`` item.
    """
    children = document.get("childListings")
    rooms: dict[str, ListingTimes] = {}
    for child in children if isinstance(children, list) else ():
        if isinstance(child, dict) and child.get("_id"):
            rooms[str(child["_id"])] = listing_times(child)
    return StayTimes(listing_times(document), rooms)


def local_date(value: Any) -> date | None:
    """Parse a listing-localized ``YYYY-MM-DD`` date, or None."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def local_dates(segment: dict[str, Any]) -> tuple[date, date] | None:
    """Return a segment's localized dates when both parse, else None."""
    check_in = local_date(segment.get("checkInDateLocalized"))
    check_out = local_date(segment.get("checkOutDateLocalized"))
    return (check_in, check_out) if check_in and check_out else None


def required_date(*holders: dict[str, Any], key: str) -> date:
    """Parse the first present localized date *key* of *holders*.

    Raises:
        PMSProviderError: If it is missing or unparsable: the stay's
            instants are built from it (issue #212).
    """
    value = next((h[key] for h in holders if h.get(key)), None)
    parsed = local_date(value)
    if parsed is None:
        msg = f"Cannot parse Guesty V3 {key} value: {value!r}"
        raise PMSProviderError(msg)
    return parsed


def _clock(value: str | None) -> time | None:
    """Parse an ``HH:mm`` time, or None."""
    match = _HH_MM.fullmatch(value.strip()) if value else None
    if match is None:
        return None
    return time(int(match["hour"]), int(match["minute"]))


def _zone(name: str | None) -> ZoneInfo | None:
    """Return the IANA zone *name*, or None when absent or unknown."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return None


def _midnight(day: date) -> datetime:
    """Return the date-only sentinel for *day*."""
    return datetime.combine(day, time(), tzinfo=UTC)


def stay_instants(
    check_in: tuple[date, ListingTimes], check_out: tuple[date, ListingTimes]
) -> tuple[datetime, datetime]:
    """Build a stay's UTC check-in and check-out instants.

    Args:
        check_in: The local check-in date and the times of its room.
        check_out: The local check-out date and the times of its room.

    Returns:
        Both instants timed, or both date-only when either time or zone
        is unusable, or either instant falls on midnight UTC (which would
        read back as a date-only stay on the wrong day).
    """
    (in_day, in_times), (out_day, out_times) = check_in, check_out
    in_zone, out_zone = _zone(in_times.timezone), _zone(out_times.timezone)
    in_clock, out_clock = _clock(in_times.check_in), _clock(out_times.check_out)
    if in_zone and out_zone and in_clock and out_clock:
        start = datetime.combine(in_day, in_clock, tzinfo=in_zone).astimezone(UTC)
        end = datetime.combine(out_day, out_clock, tzinfo=out_zone).astimezone(UTC)
        if start.time() != time() and end.time() != time():
            return start, end
    return _midnight(in_day), _midnight(out_day)


def localized_stay(
    check_in_holders: Sequence[dict[str, Any]],
    check_out_holders: Sequence[dict[str, Any]],
    check_in_times: ListingTimes,
    check_out_times: ListingTimes,
) -> tuple[tuple[date, date], tuple[datetime, datetime]]:
    """Return a stay's required localized dates and its built instants.

    Args:
        check_in_holders: Objects whose first ``checkInDateLocalized``
            is used (e.g. the item, then its first segment).
        check_out_holders: Likewise for ``checkOutDateLocalized``.
        check_in_times: Times of the check-in room.
        check_out_times: Times of the check-out room.

    Raises:
        PMSProviderError: If a localized date is unusable.

    Returns:
        The local dates and the instants (see ``stay_instants``).
    """
    dates = (
        required_date(*check_in_holders, key="checkInDateLocalized"),
        required_date(*check_out_holders, key="checkOutDateLocalized"),
    )
    instants = stay_instants((dates[0], check_in_times), (dates[1], check_out_times))
    return dates, instants
