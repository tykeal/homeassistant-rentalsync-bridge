# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Apply ``REATTRIBUTED`` absence verdicts to stored bookings (research R5).

A reservation the search no longer returns, but whose by-IDs lookup still
places a segment in the listing, keeps the stored rows matching its
intervals.  The by-IDs payload has only listing-localized dates, so each
kept row is re-anchored from its stored instants; guest-owned details are
kept only while the guest is demonstrably the same one.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any

from src.providers.base import (
    AbsenceResult,
    AbsenceVerdict,
    PMSGuest,
    PMSProviderError,
    PMSReservation,
)
from src.utils.stay_time import has_meaningful_times, to_local_date

if TYPE_CHECKING:
    from collections.abc import Iterable
    from zoneinfo import ZoneInfo

    from src.models.booking import Booking

logger = logging.getLogger(__name__)

GUEST_SOURCE_ID_KEY = "_guest_source_id"
GUEST_NAME_KEY = "_guest_name"
CHANGE_KEY_KEY = "_change_key"
_GUEST_KEYS = frozenset(
    {
        "guest_phone",
        "guest_email",
        "guest_phone_last4",
        GUEST_SOURCE_ID_KEY,
        GUEST_NAME_KEY,
    }
)
_PHONE_LAST_DIGITS = 4

GuestLookup = Callable[[str], Awaitable[PMSGuest | None]]


def _as_utc(value: datetime) -> datetime:
    """Return a stored (SQLite-naive UTC) datetime as aware UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _ends_after_start(check_in: datetime, check_out: datetime, tz: ZoneInfo) -> bool:
    """Return whether the stay ends after it starts, as the calendar exports it.

    Timed stays compare instants; all-day and mixed stays compare the
    exported dates.
    """
    if has_meaningful_times(check_in, check_out):
        return _as_utc(check_out) > _as_utc(check_in)
    return to_local_date(check_out, tz) > to_local_date(check_in, tz)


def reanchor(stored: datetime, target: date, tz: ZoneInfo) -> datetime:
    """Move a stored instant to *target*'s local date, keeping its local time.

    Args:
        stored: The stored instant (naive values are UTC).
        target: The listing-local date it must fall on.
        tz: The listing's timezone.

    Returns:
        *stored* unchanged when it already falls on *target*; otherwise
        *target* at the stored local time of day, as UTC.  A midnight-UTC
        value is an all-day date (as in ``stay_time.to_local_date``) and
        moves to *target* at midnight UTC.
    """
    utc = _as_utc(stored)
    if utc.time() == time(0, 0):
        if utc.date() == target:
            return stored
        return datetime.combine(target, time(0, 0), tzinfo=UTC)
    local = utc.astimezone(tz)
    if local.date() == target:
        return stored
    moved = datetime.combine(target, local.time(), tzinfo=tz)
    return moved.astimezone(UTC)


@dataclass(frozen=True)
class GuestDetails:
    """Guest-owned values to publish on a re-attributed row.

    ``data`` holds the guest-owned enrichment values (empty when nothing
    may be published); ``name`` is the guest name, None for the
    placeholder.
    """

    name: str | None
    data: dict[str, Any]


@dataclass(frozen=True)
class RowUpdate:
    """One stored row kept by a re-attribution, with its new values.

    ``guest`` is None when the stored guest details stay as they are.
    """

    booking: Booking
    check_in: datetime
    check_out: datetime
    status: str
    guest: GuestDetails | None


@dataclass(frozen=True)
class Reattribution:
    """The rows kept for one re-attributed reservation."""

    reservation_id: str
    rows: tuple[RowUpdate, ...]


def _phone_last4(phone: str | None) -> str | None:
    """Return a phone number's last four digits, or None."""
    digits = "".join(ch for ch in phone or "" if ch.isdigit())
    return digits[-_PHONE_LAST_DIGITS:] if len(digits) >= _PHONE_LAST_DIGITS else None


class _Guests:
    """Resolve each guest at most once per sync."""

    def __init__(self, lookup: GuestLookup) -> None:
        """Initialize with the provider's guest lookup."""
        self._lookup = lookup
        self._cache: dict[str, GuestDetails] = {}

    async def details(self, guest_id: str) -> GuestDetails:
        """Return the publishable details of *guest_id* (empty on failure)."""
        if guest_id not in self._cache:
            try:
                guest = await self._lookup(guest_id)
            except PMSProviderError:
                logger.warning(
                    "Failed to resolve guest %s for a re-attributed "
                    "reservation; publishing no guest contact",
                    guest_id,
                )
                guest = None
            data: dict[str, Any] = {}
            if guest is not None:
                data[GUEST_SOURCE_ID_KEY] = guest_id
                if guest.full_name:
                    data[GUEST_NAME_KEY] = guest.full_name
                last4 = _phone_last4(guest.phone)
                if last4:
                    data["guest_phone_last4"] = last4
                if guest.phone:
                    data["guest_phone"] = guest.phone
                if guest.email:
                    data["guest_email"] = guest.email
            self._cache[guest_id] = GuestDetails(
                guest.full_name if guest is not None else None, data
            )
        return self._cache[guest_id]


async def _guest_for(
    booking: Booking, guest_id: str | None, guests: _Guests
) -> GuestDetails | None:
    """Gate a row's guest-owned details on the guest's identity (FR-031).

    Returns:
        None to keep the stored details (same guest); otherwise what to
        publish instead (the new guest's, or nothing).
    """
    stored = booking.enrichment_data or {}
    source = stored.get(GUEST_SOURCE_ID_KEY)
    if guest_id is not None and source == guest_id:
        return None
    if guest_id is None or source is None:
        return GuestDetails(None, {})
    return await guests.details(guest_id)


async def plan_reattributions(
    *,
    rows: Iterable[Booking],
    absence: dict[str, AbsenceResult],
    tz: ZoneInfo,
    normalise_status: Callable[[object, str | None], str],
    get_guest: GuestLookup,
    listing_pms_id: str,
) -> dict[str, Reattribution]:
    """Work out every re-attribution before anything is written.

    A candidate whose kept row would end before it starts after
    re-anchoring is downgraded to ``UNCONFIRMED`` in *absence* (in place),
    so none of its rows changes this cycle.

    Args:
        rows: The listing's stored bookings.
        absence: ``confirm_absent`` results by reservation ID.
        tz: The listing's timezone.
        normalise_status: ``SyncService._normalize_booking_status``.
        get_guest: The provider's guest lookup.
        listing_pms_id: Listing being synced, for messages.

    Returns:
        Re-attributions by reservation ID.
    """
    stored: dict[str, Booking] = {
        b.pms_booking_id: b for b in rows if b.status != "cancelled"
    }
    planner = _Planner(stored, tz, normalise_status, _Guests(get_guest), listing_pms_id)
    plans: dict[str, Reattribution] = {}
    for rid, result in sorted(absence.items()):
        reservation = result.reservation
        if result.verdict is not AbsenceVerdict.REATTRIBUTED or reservation is None:
            continue
        updates = await _row_updates(planner, rid, reservation)
        if updates is None:
            absence[rid] = AbsenceResult(
                AbsenceVerdict.UNCONFIRMED, detail="re-anchored dates invalid"
            )
            continue
        plans[rid] = Reattribution(rid, updates)
    return plans


@dataclass(frozen=True)
class _Planner:
    """What one listing's re-attribution planning needs."""

    stored: dict[str, Booking]
    tz: ZoneInfo
    normalise_status: Callable[[object, str | None], str]
    guests: _Guests
    listing_pms_id: str


async def _row_updates(
    ctx: _Planner, rid: str, reservation: PMSReservation
) -> tuple[RowUpdate, ...] | None:
    """Return the kept rows' new values, or None if any is invalid."""
    stored, tz, guests = ctx.stored, ctx.tz, ctx.guests
    listing_pms_id = ctx.listing_pms_id
    status = ctx.normalise_status(reservation.status, rid)
    updates: list[RowUpdate] = []
    for interval in reservation.intervals:
        booking = stored.get(f"{rid}::{interval.booking_suffix}")
        if booking is None or interval.local_dates is None:
            logger.info(
                "Reservation %s now also occupies room %s of listing %s, but "
                "only by-IDs data describes it; it is not published there "
                "until the search returns it",
                rid,
                interval.room_id,
                listing_pms_id,
            )
            continue
        check_in = reanchor(booking.check_in_date, interval.local_dates[0], tz)
        check_out = reanchor(booking.check_out_date, interval.local_dates[1], tz)
        if not _ends_after_start(check_in, check_out, tz):
            logger.warning(
                "Keeping reservation %s on listing %s unchanged: re-anchoring "
                "booking %s to its new dates would end it before it starts",
                rid,
                listing_pms_id,
                booking.pms_booking_id,
            )
            return None
        updates.append(
            RowUpdate(
                booking,
                check_in,
                check_out,
                status,
                await _guest_for(booking, reservation.guest_id, guests),
            )
        )
    return tuple(updates)


def apply_row(update: RowUpdate) -> bool:
    """Write one kept row's new values onto the stored booking.

    Reservation-owned custom data stays as stored; guest-owned values are
    replaced only when the guest gate says so.  A NULL ``enrichment_data``
    (never stored) is never turned into a dict.

    Args:
        update: The row and its new values.

    Returns:
        True if any stored value changed.
    """
    booking = update.booking
    before = (
        booking.check_in_date,
        booking.check_out_date,
        booking.status,
        booking.guest_name,
        booking.guest_phone_last4,
        booking.custom_data,
        booking.enrichment_data,
    )
    if update.check_in is not booking.check_in_date:
        booking.check_in_date = update.check_in
    if update.check_out is not booking.check_out_date:
        booking.check_out_date = update.check_out
    booking.status = update.status
    if update.guest is not None:
        guest = update.guest
        exported = {
            k: v
            for k, v in guest.data.items()
            if k not in (GUEST_SOURCE_ID_KEY, GUEST_NAME_KEY)
        }
        custom = {
            k: v for k, v in (booking.custom_data or {}).items() if k not in _GUEST_KEYS
        }
        merged = {**exported, **custom}
        booking.custom_data = (
            merged if merged or booking.custom_data is not None else None
        )
        if booking.enrichment_data is not None:
            booking.enrichment_data = {
                **{
                    k: v
                    for k, v in booking.enrichment_data.items()
                    if k not in _GUEST_KEYS
                },
                **guest.data,
            }
        booking.guest_name = guest.name
        booking.guest_phone_last4 = guest.data.get("guest_phone_last4")
    if booking.enrichment_data and CHANGE_KEY_KEY in booking.enrichment_data:
        # The stored values are carried forward, not a fresh read of this
        # reservation: the next search must enrich it again (research R9).
        booking.enrichment_data = {
            k: v for k, v in booking.enrichment_data.items() if k != CHANGE_KEY_KEY
        }
    booking.last_fetched_at = datetime.now(UTC)
    after = (
        booking.check_in_date,
        booking.check_out_date,
        booking.status,
        booking.guest_name,
        booking.guest_phone_last4,
        booking.custom_data,
        booking.enrichment_data,
    )
    return after != before
