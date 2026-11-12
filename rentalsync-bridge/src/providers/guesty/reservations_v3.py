# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty Reservations V3 source (``/v1/reservations-v3``)."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from src.providers.base import PMSProviderError, PMSReservation
from src.providers.guesty.legacy_reservations import (
    _CLASSIFIED_STATUSES,
    DEFAULT_LOOKAHEAD_DAYS,
    _guard_unclassified,
    _overlaps_window,
)
from src.providers.guesty.v3_mapping import map_search_item, owned_by
from src.providers.guesty.v3_search import combined_search, pack_chunks
from src.utils.datetime_utils import ensure_utc

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.providers.guesty.sources import ListingScopesFn, RequestFn

logger = logging.getLogger(__name__)

# Retention (``OLD_BOOKING_RETENTION_DAYS``, 90) plus one day is the binding
# lookback: the sync discards anything that checked out before retention, so
# fetching further back only spends pages (research R3).
V3_LOOKBACK_DAYS = 91

# Pad on each side of the localized-date filter so one combined search over
# listings in any timezone stays a superset of the instant-based window.
_TIMEZONE_PAD = timedelta(days=1)

Scopes = tuple[frozenset[str], frozenset[str]]


def _utcnow() -> datetime:
    """Return the current UTC instant (patched by tests)."""
    return datetime.now(UTC)


def search_window(now: datetime) -> tuple[date, date]:
    """Return the ``(D_start, D_end)`` stay-date filter bounds (research R3).

    Args:
        now: Reference instant.

    Returns:
        Localized-date bounds for ``filter[checkOut][gte]`` and
        ``filter[checkIn][lte]``.
    """
    now = ensure_utc(now)
    start = (now - timedelta(days=V3_LOOKBACK_DAYS)).date() - _TIMEZONE_PAD
    end = (now + timedelta(days=DEFAULT_LOOKAHEAD_DAYS)).date() + _TIMEZONE_PAD
    return start, end


class V3ReservationSource:
    """Fetch reservations through one combined V3 search per sync cycle.

    The tracked listings' fetch scopes are searched together once
    (research R2); each listing's ``fetch`` then partitions the cached
    result.  A failed search is cached as a failure, so every listing in
    the cycle keeps its last-good bookings.
    """

    def __init__(
        self,
        *,
        request: RequestFn,
        listing_scopes: ListingScopesFn,
    ) -> None:
        """Initialize the source.

        Args:
            request: The provider's authenticated, retrying request method.
            listing_scopes: Resolves a listing's ``(fetch_ids, scope_ids)``.
        """
        self._request = request
        self._listing_scopes = listing_scopes
        self._tracked: set[str] = set()
        self._scopes: dict[str, Scopes | PMSProviderError] = {}
        self._result: list[dict[str, Any]] | PMSProviderError | None = None

    def begin_cycle(self, listing_pms_ids: Sequence[str]) -> None:
        """Declare the cycle's listings and drop the previous cycle's state.

        Args:
            listing_pms_ids: PMS IDs of the listings about to be synced.
        """
        self._tracked = set(listing_pms_ids)
        self._scopes = {}
        self._result = None

    async def _scope(self, listing_pms_id: str) -> Scopes | PMSProviderError:
        """Resolve (once per cycle) a listing's scopes or its failure."""
        if listing_pms_id not in self._scopes:
            try:
                self._scopes[listing_pms_id] = await self._listing_scopes(
                    listing_pms_id
                )
            except PMSProviderError as exc:
                self._scopes[listing_pms_id] = exc
        return self._scopes[listing_pms_id]

    async def _items(self) -> list[dict[str, Any]]:
        """Return the cycle's combined search result, searching once.

        Raises:
            PMSProviderError: If the combined search failed.
        """
        if self._result is None:
            families = []
            for listing in sorted(self._tracked):
                scope = await self._scope(listing)
                # A listing whose scope failed still filters on its own ID.
                fetch_ids = (
                    frozenset({listing})
                    if isinstance(scope, PMSProviderError)
                    else scope[0] | {listing}
                )
                families.append(fetch_ids)
            try:
                items, pages = await combined_search(
                    self._request, pack_chunks(families), search_window(_utcnow())
                )
                logger.debug(
                    "Guesty V3 combined search: %d reservation(s) in %d page(s) "
                    "for %d listing(s)",
                    len(items),
                    pages,
                    len(self._tracked),
                )
                self._result = items
            except PMSProviderError as exc:
                self._result = exc
        if isinstance(self._result, PMSProviderError):
            raise self._result
        return self._result

    async def fetch(
        self,
        listing_pms_id: str,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[PMSReservation]:
        """Return a listing's reservations from the combined search.

        Args:
            listing_pms_id: Guesty listing ID.
            start_date: Start of the stay window (default: the V3 lookback).
            end_date: End of the stay window (default: 365 days ahead).

        Returns:
            The reservations the listing owns whose stay overlaps the
            window.

        Raises:
            PMSProviderError: If the listing's scope or the combined search
                failed, or an owned item cannot be mapped safely.
        """
        if listing_pms_id not in self._tracked:
            self._tracked.add(listing_pms_id)
            self._result = None

        items = await self._items()
        scope = await self._scope(listing_pms_id)
        if isinstance(scope, PMSProviderError):
            raise scope
        scope_ids = scope[1]

        now = _utcnow()
        start = ensure_utc(start_date or now - timedelta(days=V3_LOOKBACK_DAYS))
        end = ensure_utc(end_date or now + timedelta(days=DEFAULT_LOOKAHEAD_DAYS))

        reservations: list[PMSReservation] = []
        unclassified = 0
        for item in items:
            if not owned_by(item, scope_ids):
                continue
            mapped = map_search_item(item, listing_pms_id, scope_ids)
            if mapped is None or not _overlaps_window(mapped, start, end):
                continue
            if mapped.status not in _CLASSIFIED_STATUSES:
                unclassified += 1
            reservations.append(mapped)
        _guard_unclassified(unclassified, len(reservations), listing_pms_id)
        return reservations
