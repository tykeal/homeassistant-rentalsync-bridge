# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Verify reservation identifiers survive the Guesty V3 switch (FR-006)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.providers.base import PMSProviderError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from src.providers.base import PMSReservation

CONFIRMATION_CODE_KEY = "confirmationCode"


def verify_reservation_identity(
    listing_pms_id: str,
    stored: Iterable[tuple[str, object]],
    reservations: Sequence[PMSReservation],
) -> int:
    """Check stored reservations are still known by the same IDs (R14).

    Booking IDs, and so event UIDs, derive from the reservation ID. A
    stored reservation whose confirmation code reappears under a different
    ID, while its own ID is gone from the snapshot, means the identifiers
    changed between APIs: syncing would retire every published event and
    republish it under a new UID.

    Args:
        listing_pms_id: Listing being synced, for the message.
        stored: ``(base reservation ID, confirmation code)`` of each active
            stored booking.
        reservations: The listing's retention-filtered snapshot.

    Returns:
        How many stored reservations were found under their stored IDs.

    Raises:
        PMSProviderError: If a stored reservation was re-keyed.
    """
    snapshot_ids = {r.pms_booking_id for r in reservations}
    by_code: dict[str, set[str]] = {}
    for reservation in reservations:
        code = reservation.custom_data.get(CONFIRMATION_CODE_KEY)
        if isinstance(code, str) and code:
            by_code.setdefault(code, set()).add(reservation.pms_booking_id)

    matched: set[str] = set()
    for base_id, code in stored:
        if not isinstance(code, str) or code not in by_code:
            continue
        current = by_code[code]
        if base_id in current:
            matched.add(base_id)
        elif base_id not in snapshot_ids:
            msg = (
                f"Guesty reservation identifiers changed between APIs for "
                f"listing {listing_pms_id}: stored reservation {base_id} "
                f"now appears as {', '.join(sorted(current))} (confirmation "
                f"code {code}). Nothing was changed. Set "
                f"guesty_reservations_api to 'legacy' and report this issue."
            )
            raise PMSProviderError(msg)
    return len(matched)
