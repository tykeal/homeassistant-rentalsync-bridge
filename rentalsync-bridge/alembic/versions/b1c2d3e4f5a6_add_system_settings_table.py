# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Add system_settings table.

Revision ID: b1c2d3e4f5a6
Revises: a9743e0fe547
Create Date: 2026-02-05 19:30:00.000000+00:00

"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a9743e0fe547"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=False, default=5),
        sa.Column(
            "settings_key", sa.String(length=50), nullable=False, unique=True
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            default=datetime.now(UTC),
        ),
    )

    # Insert default settings row
    op.execute(
        "INSERT INTO system_settings (id, sync_interval_minutes, settings_key, "
        f"updated_at) VALUES (1, 5, 'default', '{datetime.now(UTC).isoformat()}')"
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_table("system_settings")
