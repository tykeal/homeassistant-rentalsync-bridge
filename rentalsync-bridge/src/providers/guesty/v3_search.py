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
from src.providers.guesty.sources import GuestyHTTPError
from src.providers.guesty.v3_diagnostics import (
    log_chunk,
    log_search_plan,
    log_search_result,
)
from src.providers.guesty.v3_mapping import change_key, reservation_id, stay_segments

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from src.providers.guesty.sources import RequestFn

SEARCH_PATH = "/v1/reservations-v3/search"

# Listing IDs per search request, keeping the query well under 2 KB (FR-016a).
MAX_LISTING_IDS_PER_SEARCH = 50

# Characters of a rejected search's response body quoted in the error.
_EXCERPT_CHARS = 200

_PARTIAL = "refusing to return a partial snapshot"


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


def _page_items(
    data: Any, page: int, skip: int, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """Validate one page's envelope (research R4 checks 1-4).

    Args:
        data: The decoded response body.
        page: Zero-based page number, for messages.
        skip: The ``skip`` the page was requested with.
        limit: The ``limit`` the page was requested with.

    Returns:
        The page's items and its ``hasMore`` flag.

    Raises:
        PMSProviderError: If any envelope check fails.
    """
    results = data.get("results") if isinstance(data, dict) else None
    pagination = data.get("pagination") if isinstance(data, dict) else None
    has_more = pagination.get("hasMore") if isinstance(pagination, dict) else None
    if (
        not isinstance(results, list)
        or not all(isinstance(item, dict) for item in results)
        or not isinstance(has_more, bool)
    ):
        problem = "a malformed page"
    elif isinstance(pagination, dict) and pagination.get("skip", skip) != skip:
        problem = f"skip {pagination.get('skip')!r} for requested skip {skip}"
    elif len(results) > limit:
        problem = f"{len(results)} results for a limit of {limit}"
    elif has_more and len(results) < limit:
        problem = f"{len(results)} results with more pages to come"
    else:
        return results, has_more
    msg = (
        f"Guesty Reservations V3 search returned {problem} on page "
        f"{page + 1}; {_PARTIAL}"
    )
    raise PMSProviderError(msg)


def _check_items(items: list[dict[str, Any]], seen: list[str], page: int) -> None:
    """Check items' identity, order and status (research R4 checks 5-7).

    Args:
        items: One page's items.
        seen: IDs of the chunk's earlier items, ascending; extended in place.
        page: Zero-based page number, for messages.

    Raises:
        PMSProviderError: On a missing ID, status or stay, a repeated ID,
            or an ID not strictly after the previous one.
    """
    known = set(seen)
    for item in items:
        rid = reservation_id(item)
        stay_segments(item)
        if "status" not in item:
            msg = (
                f"Guesty reservation {rid} carries no 'status' field; refusing "
                f"to classify reservations without it — the existing calendar "
                f"data is kept instead of being replaced by an unsafe snapshot"
            )
            raise PMSProviderError(msg)
        if rid in known:
            problem = f"reservation {rid} twice"
        elif seen and rid <= seen[-1]:
            problem = f"reservation {rid} out of _id order"
        else:
            known.add(rid)
            seen.append(rid)
            continue
        msg = (
            f"Guesty Reservations V3 search returned {problem} on page "
            f"{page + 1}; the result set shifted mid-search, {_PARTIAL}"
        )
        raise PMSProviderError(msg)


async def _search_chunk(
    request: RequestFn,
    chunk: tuple[str, ...],
    window: tuple[date, date],
) -> tuple[list[dict[str, Any]], list[bool]]:
    """Page one chunk's search to completion behind the R4 guard.

    Args:
        request: The provider's authenticated request method.
        chunk: Sorted listing IDs to filter on.
        window: ``(D_start, D_end)`` localized stay-date bounds.

    Returns:
        The chunk's raw items and each fetched page's ``hasMore`` flag.

    Raises:
        PMSProviderError: On API failure (after the request's own 429
            retries), any failed guard check, or paging that does not end
            within ``MAX_RESERVATION_PAGES``.
    """
    limit = DEFAULT_PAGE_LIMIT
    items: list[dict[str, Any]] = []
    seen: list[str] = []
    more: list[bool] = []
    for page in range(MAX_RESERVATION_PAGES):
        skip = page * limit
        params: dict[str, Any] = {
            "filter[listingId][in]": ",".join(chunk),
            "filter[checkOut][gte]": window[0].isoformat(),
            "filter[checkIn][lte]": window[1].isoformat(),
            "sort": "_id",
            "limit": limit,
            "skip": skip,
        }
        try:
            response = await request("GET", SEARCH_PATH, params=params)
        except GuestyHTTPError as exc:
            if exc.status_code != 400:  # noqa: PLR2004
                raise
            msg = (
                f"Guesty rejected the reservation search: 400 "
                f"{exc.body[:_EXCERPT_CHARS]}"
            )
            raise PMSProviderError(msg) from exc
        try:
            data = response.json()
        except ValueError:
            data = None
        results, has_more = _page_items(data, page, skip, limit)
        _check_items(results, seen, page)
        items.extend(results)
        more.append(has_more)
        if not has_more:
            return items, more
    msg = (
        f"Guesty Reservations V3 search exceeded {MAX_RESERVATION_PAGES} "
        f"pages; {_PARTIAL}"
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
    log_search_plan(chunks, window)
    merged: dict[str, tuple[str, dict[str, Any]]] = {}
    pages = duplicates = 0
    for index, chunk in enumerate(chunks, 1):
        items, more = await _search_chunk(request, chunk, window)
        log_chunk(index, len(chunks), chunk, more, len(items))
        pages += len(more)
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
            duplicates += rid in merged
            merged.setdefault(rid, (key, item))
    result = [item for _key, item in merged.values()]
    log_search_result(result, duplicates)
    return result, pages
