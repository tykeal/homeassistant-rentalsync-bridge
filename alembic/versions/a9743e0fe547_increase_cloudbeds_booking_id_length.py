# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Increase cloudbeds_booking_id column length.

Revision ID: a9743e0fe547
Revises: 0eeb46d10f64
Create Date: 2026-02-02 17:35:38.440674+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9743e0fe547"
down_revision: str | None = "0eeb46d10f64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Increase cloudbeds_booking_id column from 100 to 255 characters
    # to accommodate composite IDs for multi-room reservations
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.alter_column(
            "cloudbeds_booking_id",
            existing_type=sa.String(length=100),
            type_=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Downgrade database schema."""
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.alter_column(
            "cloudbeds_booking_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=100),
            existing_nullable=False,
        )
