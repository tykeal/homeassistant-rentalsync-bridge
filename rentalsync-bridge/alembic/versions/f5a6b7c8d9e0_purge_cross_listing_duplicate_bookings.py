# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Purge cross-listing duplicate bookings.

Before this release the Guesty provider sent a ``listingId`` query parameter
that ``/v1/reservations`` does not support.  Guesty silently ignored it and
returned the whole account, so every listing stored a row for every
reservation in the account.

Those foreign rows would otherwise linger: the sync prune loop only marks
missing bookings ``cancelled``, and its in-progress guard deliberately keeps
a stay that is currently underway, so a foreign reservation covering today
would stay in the exported calendar until after its checkout date.

Only rows that are provably foreign are removed: a booking is deleted when
its reservation ID is stored under more than one listing AND at least one of
those rows resolved to a room while this one did not.  The row that resolved
a room is the one whose listing actually owns the reservation.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-10-19 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ``pms_booking_id`` is ``<reservation id>`` or ``<reservation id>::<room id>``.
_PURGE_SQL = """
WITH ids AS (
    SELECT
        id,
        listing_id,
        room_id,
        CASE
            WHEN instr(pms_booking_id, '::') > 0
            THEN substr(pms_booking_id, 1, instr(pms_booking_id, '::') - 1)
            ELSE pms_booking_id
        END AS base_id
    FROM bookings
),
shared AS (
    SELECT base_id
    FROM ids
    GROUP BY base_id
    HAVING COUNT(DISTINCT listing_id) > 1
),
owned AS (
    SELECT DISTINCT base_id FROM ids WHERE room_id IS NOT NULL
)
DELETE FROM bookings
WHERE id IN (
    SELECT ids.id
    FROM ids
    JOIN shared ON shared.base_id = ids.base_id
    JOIN owned ON owned.base_id = ids.base_id
    WHERE ids.room_id IS NULL
)
"""


def upgrade() -> None:
    """Delete bookings duplicated onto listings that do not own them."""
    op.execute(_PURGE_SQL)


def downgrade() -> None:
    """Reverse: not supported, the duplicated rows were bogus."""
    raise NotImplementedError(
        "Downgrade not supported: the purged rows were duplicates"
    )
