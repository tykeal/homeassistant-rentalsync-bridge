# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""DEBUG diagnostics for the Reservations V3 source (FR-043, research R15).

Every line carries reservation and listing IDs, counts and enum values
only: never a guest name, contact detail, door code or note.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.providers.base import HOLD_STATUSES

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from src.providers.base import PMSReservation

logger = logging.getLogger(__name__)


def _histogram(values: Sequence[Any]) -> dict[Any, int]:
    """Return value counts sorted by value."""
    return dict(sorted(Counter(values).items()))


def log_search_plan(
    chunks: Sequence[tuple[str, ...]], window: tuple[date, date]
) -> None:
    """Log the filter IDs, chunk count and localized window of a search."""
    logger.debug(
        "Guesty V3 search: filter_ids=%d chunks=%d window=%s..%s",
        len({listing for chunk in chunks for listing in chunk}),
        len(chunks),
        window[0].isoformat(),
        window[1].isoformat(),
    )


def log_chunk(
    index: int, total: int, chunk: tuple[str, ...], has_more: list[bool], items: int
) -> None:
    """Log one chunk's pagination: pages, each page's hasMore and items."""
    logger.debug(
        "Guesty V3 search chunk %d/%d: listing_ids=%d pages=%d has_more=%s items=%d",
        index,
        total,
        len(chunk),
        len(has_more),
        has_more,
        items,
    )


def log_search_result(items: list[dict[str, Any]], duplicates: int) -> None:
    """Log totals, histograms and every reservation's segment listings."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    logger.debug(
        "Guesty V3 search result: items=%d duplicates=%d statuses=%s segments=%s",
        len(items),
        duplicates,
        _histogram([str(item.get("status")) for item in items]),
        _histogram([len(item.get("stay") or ()) for item in items]),
    )
    for item in items:
        stay = item.get("stay") or ()
        logger.debug(
            "Guesty V3 reservation %s: status=%s segments=%d listings=%s",
            item.get("reservationId"),
            item.get("status"),
            len(stay),
            ",".join(str(segment.get("listingId")) for segment in stay),
        )


@dataclass
class ListingStats:
    """What one listing's fetch kept from the combined search."""

    owned: int = 0
    in_window: int = 0
    unclassified: int = 0

    def log(self, listing_pms_id: str, reservations: Sequence[PMSReservation]) -> None:
        """Log the listing's counts, including its interval breakdown."""
        intervals = sum(max(len(r.intervals), 1) for r in reservations)
        logger.debug(
            "Guesty V3 listing %s: owned=%d in_window=%d intervals=%d "
            "extra_intervals=%d holds=%d unclassified=%d",
            listing_pms_id,
            self.owned,
            self.in_window,
            intervals,
            intervals - len(reservations),
            sum(r.status in HOLD_STATUSES for r in reservations),
            self.unclassified,
        )
