# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Build the occupancy intervals of a V3 reservation's stay segments."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time
from typing import Any

from src.providers.base import PMSOccupancyInterval
from src.providers.guesty.legacy_reservations import _parse_date
from src.providers.guesty.v3_stay_times import (
    ListingTimes,
    StayTimes,
    local_date,
    local_dates,
    required_date,
    stay_instants,
)


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


def _guests(value: Any) -> int | None:
    """Return a segment's ``guestsCount`` when it is an integral number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _adjacent(previous: dict[str, Any], segment: dict[str, Any]) -> bool:
    """Report whether a segment continues the previous one in its room."""
    if previous["listingId"] != segment["listingId"]:
        return False
    check_out, check_in = previous.get("checkOut"), segment.get("checkIn")
    if not check_out or not check_in:
        return False
    return _parse_date(check_out) == _parse_date(check_in)


def _switch_over(
    previous: dict[str, Any] | None,
    segment: dict[str, Any],
    scope_ids: frozenset[str],
    times: StayTimes,
) -> ListingTimes | None:
    """Return the times that open a room the guest moves into mid-stay.

    A segment that starts on the local date the previous segment of the
    same reservation ends is a switch-over: the room opens at the previous
    room's check-out time, so door access is continuous.  The localized
    dates decide it, since the instants are built from them and V3's raw
    times are not trusted (issue #212).

    Args:
        previous: The segment before *segment* in the stay, or None.
        segment: A validated stay segment.
        scope_ids: Listing IDs owned by the listing being synced.
        times: Listing times of the synced listing and the known rooms.

    Returns:
        The previous room's timezone with its check-out time as the
        check-in time, or None when this is no switch-over or the previous
        room's times are unknown (an untracked listing).
    """
    if previous is None:
        return None
    boundary = local_date(previous.get("checkOutDateLocalized"))
    if boundary is None or boundary != local_date(segment.get("checkInDateLocalized")):
        return None
    room = previous["listingId"]
    if segment_owned(previous, scope_ids):
        known = times.for_room(room)
    elif room in times.rooms:
        known = times.rooms[room]
    else:
        return None
    return ListingTimes(known.timezone, known.check_out, None)


def _opening(
    dates: tuple[date, date],
    room_times: ListingTimes,
    switch: ListingTimes | None,
) -> tuple[datetime, datetime]:
    """Build an interval's instants, opening at a switch-over when timed.

    Falls back to the room's own check-in (the #215 instants) when the
    switch-over times give a date-only stay.
    """
    if switch is not None:
        start, end = stay_instants((dates[0], switch), (dates[1], room_times))
        if start.time() != time():
            return start, end
    return stay_instants((dates[0], room_times), (dates[1], room_times))


def _merge(
    interval: PMSOccupancyInterval,
    segment: dict[str, Any],
    times: StayTimes,
    switch: ListingTimes | None,
) -> PMSOccupancyInterval:
    """Extend an interval by the adjacent same-room segment after it."""
    check_out_local = required_date(segment, key="checkOutDateLocalized")
    check_in_local = (
        interval.local_dates[0]
        if interval.local_dates
        else required_date(segment, key="checkInDateLocalized")
    )
    check_in, check_out = _opening(
        (check_in_local, check_out_local), times.for_room(interval.room_id), switch
    )
    counts = [
        n
        for n in (interval.guests_count, _guests(segment.get("guestsCount")))
        if n is not None
    ]
    return replace(
        interval,
        check_in=check_in,
        check_out=check_out,
        local_dates=(check_in_local, check_out_local),
        guests_count=max(counts) if counts else None,
    )


def occupancy_intervals(
    segments: list[dict[str, Any]],
    scope_ids: frozenset[str],
    *,
    with_instants: bool = True,
    times: StayTimes | None = None,
) -> tuple[PMSOccupancyInterval, ...]:
    """Build the occupancy intervals of the segments a listing owns.

    Consecutive owned segments in one room whose check-out instant is the
    next check-in are one interval; a later return to a room is another,
    and ordinals count those per room (research R6).  An interval that
    starts at a mid-stay move opens at the previous room's check-out
    time (see ``_switch_over``).

    Args:
        segments: Validated stay segments.
        scope_ids: Listing IDs owned by the listing being synced.
        with_instants: False for by-IDs segments, which carry only local
            dates; their intervals then have no UTC instants and never
            merge (an equal same-room boundary date is ambiguous there).
        times: Listing times the instants are built from (see
            ``stay_instants``); None gives date-only instants.

    Raises:
        PMSProviderError: If an owned segment's localized date is
            unusable while instants are built.

    Returns:
        The owned intervals in stay order.
    """
    times = times or StayTimes()
    seen: Counter[str] = Counter()
    intervals: list[PMSOccupancyInterval] = []
    previous: dict[str, Any] | None = None
    prior: dict[str, Any] | None = None
    switch: ListingTimes | None = None
    for segment in segments:
        owned = segment_owned(segment, scope_ids)
        if (
            owned
            and with_instants
            and previous is not None
            and _adjacent(previous, segment)
        ):
            intervals[-1] = _merge(intervals[-1], segment, times, switch)
            previous = prior = segment
            continue
        previous = segment if owned else None
        before, prior = prior, segment
        if not owned:
            continue
        room = segment["listingId"]
        seen[room] += 1
        check_in = check_out = None
        dates = local_dates(segment)
        if with_instants:
            switch = _switch_over(before, segment, scope_ids, times)
            dates = (
                required_date(segment, key="checkInDateLocalized"),
                required_date(segment, key="checkOutDateLocalized"),
            )
            check_in, check_out = _opening(dates, times.for_room(room), switch)
        intervals.append(
            PMSOccupancyInterval(
                room_id=room,
                ordinal=seen[room],
                check_in=check_in,
                check_out=check_out,
                local_dates=dates,
                guests_count=_guests(segment.get("guestsCount")),
            )
        )
    return tuple(intervals)
