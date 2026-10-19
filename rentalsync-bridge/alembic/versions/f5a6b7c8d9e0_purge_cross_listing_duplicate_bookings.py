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
would stay in a listing-level exported calendar until after its checkout
date.

Preservation bias (deliberate)
------------------------------
This migration is destructive and irreversible, so the two failure modes are
not symmetric: a stale row left behind is cosmetic and the next sync or a
later migration can clear it, while a deleted real booking is silently
unrecoverable.  Every ambiguous row is therefore **kept**, and a row is
deleted only on positive evidence that it duplicates a *current* room
assignment held by a different listing:

* the row carries no room of its own (``room_id IS NULL``), and
* another listing holds a live, room-assigned row for the same reservation,
  and
* the row's own listing holds no room-assigned row for that reservation.

A ``cancelled`` sibling never counts as evidence of current ownership: a
historical room assignment does not prove who owns the stay now.  That is
exactly the unassign/reassign case on a multi-unit listing, where the owner
can retain a cancelled ``<id>::<room>`` row alongside its live, room-less
``<id>`` row — the live row must survive.  Unassigned and reassigned
bookings are preserved for the same reason.

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
        status,
        CASE
            WHEN instr(pms_booking_id, '::') > 0
            THEN substr(pms_booking_id, 1, instr(pms_booking_id, '::') - 1)
            ELSE pms_booking_id
        END AS base_id
    FROM bookings
),
-- Positive evidence of current ownership: a live row that is assigned to a
-- room right now.  Cancelled rows are excluded because a historical room
-- assignment does not prove present ownership.
owner_rows AS (
    SELECT base_id, listing_id
    FROM ids
    WHERE room_id IS NOT NULL
      AND status <> 'cancelled'
)
DELETE FROM bookings
WHERE id IN (
    SELECT ids.id
    FROM ids
    WHERE ids.room_id IS NULL
      AND EXISTS (
          SELECT 1 FROM owner_rows
          WHERE owner_rows.base_id = ids.base_id
            AND owner_rows.listing_id <> ids.listing_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM ids AS sibling
          WHERE sibling.base_id = ids.base_id
            AND sibling.listing_id = ids.listing_id
            AND sibling.room_id IS NOT NULL
      )
)
"""


def upgrade() -> None:
    """Delete bookings proven to duplicate another listing's assignment."""
    op.execute(_PURGE_SQL)


def downgrade() -> None:
    """Reverse: not supported, the duplicated rows were bogus."""
    raise NotImplementedError(
        "Downgrade not supported: the purged rows were duplicates"
    )
