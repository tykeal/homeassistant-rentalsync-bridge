# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guesty Reservations V3 source (``/v1/reservations-v3``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.providers.base import PMSProviderError, PMSReservation

if TYPE_CHECKING:
    from datetime import datetime

    from src.providers.guesty.sources import ListingScopesFn, RequestFn


class V3ReservationSource:
    """Fetch reservations through Guesty's Reservations V3 API.

    Not implemented yet: ``fetch`` raises ``PMSProviderError`` so selecting
    ``v3`` aborts the listing's sync before any snapshot reaches the prune
    loop.  It never returns an empty (or partial) snapshot.
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

    async def fetch(
        self,
        listing_pms_id: str,
        *,
        start_date: datetime | None = None,  # noqa: ARG002
        end_date: datetime | None = None,  # noqa: ARG002
    ) -> list[PMSReservation]:
        """Refuse to fetch until V3 retrieval is implemented.

        Args:
            listing_pms_id: Guesty listing ID.
            start_date: Unused.
            end_date: Unused.

        Raises:
            PMSProviderError: Always; no request is made.
        """
        msg = (
            f"Guesty Reservations V3 retrieval is not available in this "
            f"build (listing {listing_pms_id}); set guesty_reservations_api "
            f"to 'legacy'. No bookings were changed."
        )
        raise PMSProviderError(msg)
