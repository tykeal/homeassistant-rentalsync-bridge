# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Add enrichment_data column to bookings.

Stores the subset of ``custom_data`` that came from provider enrichment
endpoints (custom fields and guest lookup), so a later sync can reuse it
without resurrecting reservation fields the user has since deleted
(issue #128).  NULL means there is nothing to reuse.

Revision ID: a7b8c9d0e1f2
Revises: f5a6b7c8d9e0
Create Date: 2026-10-20 21:45:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.sqlite import JSON

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.add_column(
        "bookings",
        sa.Column("enrichment_data", JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_column("bookings", "enrichment_data")
