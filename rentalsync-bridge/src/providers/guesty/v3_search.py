# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""The combined, listing-filtered Reservations V3 search (research R2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.providers.base import PMSProviderError
from src.providers.guesty.legacy_reservations import (
    DEFAULT_PAGE_LIMIT,
    MAX_RESERVATION_PAGES,
)
from src.providers.guesty.v3_mapping import change_key, reservation_id

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from src.providers.guesty.sources import RequestFn

SEARCH_PATH = "/v1/reservations-v3/search"

# Listing IDs per search request, keeping the query well under 2 KB (FR-016a).
MAX_LISTING_IDS_PER_SEARCH = 50


def pack_chunks(
    families: Iterable[frozenset[str]],
    limit: int = MAX_LISTING_IDS_PER_SEARCH,
) -> list[tuple[str, ...]]:
    """Pack listing-ID families into sorted chunks of at most *limit* IDs.

    Overlapping families are merged first, so a reservation's first-segment
    unit and unit type always share a chunk.  Families are then packed
    greedily in order of their smallest ID; only a family larger than
    *limit* is split.

    Args:
        families: Each tracked listing's fetch scope.
        limit: Maximum IDs per chunk.

    Returns:
        Chunks of sorted listing IDs.
    """
    merged: list[set[str]] = []
    for family in families:
        group = set(family)
        for other in [m for m in merged if m & group]:
            group |= other
            merged.remove(other)
        merged.append(group)

    chunks: list[list[str]] = []
    current: list[str] = []
    for group in sorted(merged, key=min):
        ids = sorted(group)
        if len(ids) > limit:
            if current:
                chunks.append(current)
                current = []
            chunks.extend(ids[i : i + limit] for i in range(0, len(ids), limit))
            continue
        if len(current) + len(ids) > limit:
            chunks.append(current)
            current = []
        current.extend(ids)
    if current:
        chunks.append(current)
    return [tuple(sorted(chunk)) for chunk in chunks]


async def _search_chunk(
    request: RequestFn,
    chunk: tuple[str, ...],
    window: tuple[date, date],
) -> tuple[list[dict[str, Any]], int]:
    """Page one chunk's search to completion.

    Args:
        request: The provider's authenticated request method.
        chunk: Sorted listing IDs to filter on.
        window: ``(D_start, D_end)`` localized stay-date bounds.

    Returns:
        The chunk's raw items and the number of pages fetched.

    Raises:
        PMSProviderError: On API failure, a malformed page, or paging that
            does not end within ``MAX_RESERVATION_PAGES``.
    """
    items: list[dict[str, Any]] = []
    for page in range(MAX_RESERVATION_PAGES):
        params: dict[str, Any] = {
            "filter[listingId][in]": ",".join(chunk),
            "filter[checkOut][gte]": window[0].isoformat(),
            "filter[checkIn][lte]": window[1].isoformat(),
            "sort": "_id",
            "limit": DEFAULT_PAGE_LIMIT,
            "skip": page * DEFAULT_PAGE_LIMIT,
        }
        response = await request("GET", SEARCH_PATH, params=params)
        data = response.json()
        results = data.get("results") if isinstance(data, dict) else None
        pagination = data.get("pagination") if isinstance(data, dict) else None
        has_more = pagination.get("hasMore") if isinstance(pagination, dict) else None
        if (
            not isinstance(results, list)
            or not all(isinstance(item, dict) for item in results)
            or not isinstance(has_more, bool)
        ):
            msg = (
                f"Guesty Reservations V3 search returned a malformed page "
                f"{page + 1}; refusing to return a partial snapshot"
            )
            raise PMSProviderError(msg)
        items.extend(results)
        if not has_more:
            return items, page + 1
    msg = (
        f"Guesty Reservations V3 search exceeded {MAX_RESERVATION_PAGES} "
        f"pages; refusing to return a partial snapshot"
    )
    raise PMSProviderError(msg)


async def combined_search(
    request: RequestFn,
    chunks: list[tuple[str, ...]],
    window: tuple[date, date],
) -> tuple[list[dict[str, Any]], int]:
    """Run every chunk's search and merge the results.

    Args:
        request: The provider's authenticated request method.
        chunks: Listing-ID chunks (see ``pack_chunks``).
        window: ``(D_start, D_end)`` localized stay-date bounds.

    Returns:
        Items de-duplicated by ``reservationId``, and the pages fetched.

    Raises:
        PMSProviderError: If any chunk fails, or two chunks return
            different copies of one reservation (an FR-011 shift).
    """
    merged: dict[str, tuple[str, dict[str, Any]]] = {}
    pages = 0
    for chunk in chunks:
        items, chunk_pages = await _search_chunk(request, chunk, window)
        pages += chunk_pages
        for item in items:
            rid = reservation_id(item)
            key = change_key(item)
            if rid in merged and merged[rid][0] != key:
                msg = (
                    f"Guesty reservation {rid} changed between split search "
                    f"requests; refusing an inconsistent snapshot (retry on "
                    f"the next sync cycle)"
                )
                raise PMSProviderError(msg)
            merged.setdefault(rid, (key, item))
    return [item for _key, item in merged.values()], pages
