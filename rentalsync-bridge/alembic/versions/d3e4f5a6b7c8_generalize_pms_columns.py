# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Generalize Cloudbeds-specific columns to provider-agnostic names.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-02-10 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename cloudbeds-specific columns to provider-agnostic names."""
    # --- oauth_credentials: add new columns ---
    with op.batch_alter_table("oauth_credentials", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "pms_type",
                sa.String(length=20),
                nullable=False,
                server_default="cloudbeds",
            )
        )
        batch_op.add_column(
            sa.Column(
                "token_request_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "token_request_window_start",
                sa.DateTime(),
                nullable=True,
            )
        )

    # --- listings: rename cloudbeds_id -> pms_id ---
    with op.batch_alter_table("listings", schema=None) as batch_op:
        batch_op.alter_column(
            "cloudbeds_id",
            new_column_name="pms_id",
            existing_type=sa.String(length=100),
            existing_nullable=False,
        )

    # --- bookings: rename cloudbeds_booking_id -> pms_booking_id ---
    # Step 1: Rename column (constraint is preserved automatically)
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.alter_column(
            "cloudbeds_booking_id",
            new_column_name="pms_booking_id",
            existing_type=sa.String(length=255),
            existing_nullable=False,
        )

    # Step 2: Rename constraint in a separate batch operation
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_booking_listing_cloudbeds", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_booking_listing_pms",
            ["listing_id", "pms_booking_id"],
        )

    # --- rooms: rename cloudbeds_room_id -> pms_room_id ---
    with op.batch_alter_table("rooms", schema=None) as batch_op:
        batch_op.alter_column(
            "cloudbeds_room_id",
            new_column_name="pms_room_id",
            existing_type=sa.String(length=100),
            existing_nullable=False,
        )

    with op.batch_alter_table("rooms", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_room_listing_cloudbeds", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_room_listing_pms",
            ["listing_id", "pms_room_id"],
        )


def downgrade() -> None:
    """Downgrade is not supported for this migration."""
    raise NotImplementedError(
        "Downgrade is not supported for the PMS column "
        "generalization migration."
    )
