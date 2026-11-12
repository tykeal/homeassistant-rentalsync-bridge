# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Reservation-source strategy seam for the Guesty provider."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol

import httpx

from src.providers.base import PMSProviderError, PMSReservation

# ``GuestyProvider._request``: authenticated, rate-limit aware HTTP call.
RequestFn = Callable[..., Awaitable[httpx.Response]]

# ``GuestyProvider._listing_scopes``: ``(fetch_ids, scope_ids)`` for a listing.
ListingScopesFn = Callable[[str], Awaitable[tuple[frozenset[str], frozenset[str]]]]


class GuestyHTTPError(PMSProviderError):
    """A Guesty API error response (other than 401, 403 and 429)."""

    def __init__(self, message: str, *, status_code: int, body: str) -> None:
        """Initialize the error.

        Args:
            message: Human-readable message.
            status_code: The HTTP status code.
            body: The response body text.
        """
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ReservationSource(Protocol):
    """A strategy that fetches one listing's reservation snapshot."""

    async def fetch(
        self,
        listing_pms_id: str,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[PMSReservation]:
        """Return the listing's complete reservation snapshot.

        Args:
            listing_pms_id: Guesty listing ID.
            start_date: Start of the stay window (source default if None).
            end_date: End of the stay window (source default if None).

        Returns:
            Every reservation of the listing overlapping the window.

        Raises:
            PMSProviderError: Whenever a complete snapshot cannot be
                returned; a partial snapshot is never returned.
        """
        ...
