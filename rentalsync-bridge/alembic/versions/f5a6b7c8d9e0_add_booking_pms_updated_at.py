# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Add pms_updated_at column to bookings.

Stores the provider-reported "last modified" instant for a reservation so
the sync can detect unchanged reservations and skip redundant enrichment
API calls (issue #128).  NULL means "unknown", which forces a re-fetch.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-10-20 21:10:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.add_column(
        "bookings",
        sa.Column("pms_updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_column("bookings", "pms_updated_at")
