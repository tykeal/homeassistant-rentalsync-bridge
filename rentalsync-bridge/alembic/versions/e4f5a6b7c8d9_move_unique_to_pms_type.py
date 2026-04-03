# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Move unique constraint from client_id to pms_type.

Different PMS providers are independent OAuth systems and may
legitimately share the same client_id value. The correct
constraint is one credential per pms_type.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-04-03 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


naming_convention = {
    "uq": "uq_%(table_name)s_%(column_0_name)s",
}


def upgrade() -> None:
    """Move unique constraint from client_id to pms_type."""
    with op.batch_alter_table(
        "oauth_credentials",
        naming_convention=naming_convention,
    ) as batch_op:
        batch_op.drop_constraint(
            "uq_oauth_credentials_client_id", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_oauth_credentials_pms_type", ["pms_type"]
        )


def downgrade() -> None:
    """Reverse: move unique constraint back to client_id."""
    raise NotImplementedError(
        "Downgrade not supported: would lose multi-provider data"
    )
