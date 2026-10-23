# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Shared stay-boundary rules for bookings.

A stay is either *all-day* (the PMS gave us dates only, stored as midnight
UTC) or *timed* (real check-in/check-out times).  The two are compared
differently: an all-day stay has no instant, so it is compared as a local
calendar date in the listing timezone, while a timed stay is compared as an
instant.

These rules live here, rather than in a service, so the calendar export, the
sync prune guard and the repositories can all share one implementation
without ``src/repositories/`` importing ``src/services/``.
"""

import logging
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def get_timezone(timezone_str: str) -> ZoneInfo:
    """Get ZoneInfo for timezone string with fallback to UTC.

    Args:
        timezone_str: IANA timezone identifier.

    Returns:
        ZoneInfo object, defaults to UTC on invalid timezone.
    """
    try:
        return ZoneInfo(timezone_str)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid timezone '%s', falling back to UTC", timezone_str)
        return ZoneInfo("UTC")


def has_meaningful_times(check_in: datetime, check_out: datetime) -> bool:
    """Determine if check-in/check-out carry real time information.

    When a PMS provides only dates (no times), the stored datetimes will
    have a midnight UTC time component.  We treat that as "no time
    available" and fall back to all-day semantics.

    Both datetimes must have non-midnight times to count as timed.  If only
    one has a real time (mixed case), we fall back to all-day to avoid
    timezone-conversion date shifts.

    Naive datetimes are assumed UTC (SQLite strips timezone).  Aware
    datetimes are normalised to UTC before comparison.

    Args:
        check_in: Check-in datetime (may be naive or aware).
        check_out: Check-out datetime (may be naive or aware).

    Returns:
        True if both datetimes have a non-midnight UTC time.
    """
    midnight = time(0, 0)
    ci = (
        check_in.replace(tzinfo=UTC)
        if check_in.tzinfo is None
        else check_in.astimezone(UTC)
    )
    co = (
        check_out.replace(tzinfo=UTC)
        if check_out.tzinfo is None
        else check_out.astimezone(UTC)
    )
    return ci.time() != midnight and co.time() != midnight


def to_local_date(dt: datetime, tz: ZoneInfo) -> date:
    """Convert a stored datetime to the calendar date an all-day stay means.

    For iCal all-day events this is the date that lands in
    ``DTSTART;VALUE=DATE:YYYYMMDD``.

    Args:
        dt: Input datetime (may be naive or aware).
        tz: Target timezone for date extraction.

    Returns:
        Date object for the event day.
    """
    if dt.tzinfo is None:
        # Naive datetime - assume UTC (SQLite strips timezone)
        dt = dt.replace(tzinfo=UTC)
    # When time is midnight UTC the PMS meant a pure date;
    # converting to a western timezone would shift to previous day.
    if dt.astimezone(UTC).time() == time(0, 0):
        return dt.astimezone(UTC).date()
    # Non-midnight aware datetime - convert to listing timezone
    return dt.astimezone(tz).date()


def to_local_datetime(dt: datetime, tz: ZoneInfo) -> datetime:
    """Convert a stored datetime to the listing timezone for timed stays.

    Naive datetimes are assumed to be UTC (as stored by SQLite which strips
    timezone info from UTC-aware values).

    Args:
        dt: Input datetime (may be naive or aware).
        tz: Target timezone for the listing.

    Returns:
        Timezone-aware datetime in the listing's timezone.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC).astimezone(tz)
    return dt.astimezone(tz)


def is_stay_over(
    check_in: datetime,
    check_out: datetime,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> bool:
    """Check whether a stay has finished at the reference time.

    All-day stays end when the listing-local calendar day reaches the
    check-out date; a stay checking out "tomorrow" locally is still underway
    even once UTC has rolled past midnight.  Timed stays end at the stored
    check-out instant.

    Args:
        check_in: Stored check-in datetime (naive values are UTC).
        check_out: Stored check-out datetime (naive values are UTC).
        tz: Listing timezone.
        now: Optional reference time (defaults to now in UTC).

    Returns:
        True if the stay is already over at the reference time.
    """
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)

    if not has_meaningful_times(check_in, check_out):
        return to_local_date(check_out, tz) <= reference.astimezone(tz).date()

    if check_out.tzinfo is None:
        check_out = check_out.replace(tzinfo=UTC)
    return check_out <= reference


def is_stay_in_progress(
    check_in: datetime,
    check_out: datetime,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> bool:
    """Check whether a stay is underway at the reference time.

    Args:
        check_in: Stored check-in datetime (naive values are UTC).
        check_out: Stored check-out datetime (naive values are UTC).
        tz: Listing timezone.
        now: Optional reference time (defaults to now in UTC).

    Returns:
        True if the stay has started and has not yet finished.
    """
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)

    if is_stay_over(check_in, check_out, tz, reference):
        return False

    if not has_meaningful_times(check_in, check_out):
        return to_local_date(check_in, tz) <= reference.astimezone(tz).date()

    if check_in.tzinfo is None:
        check_in = check_in.replace(tzinfo=UTC)
    return check_in <= reference
