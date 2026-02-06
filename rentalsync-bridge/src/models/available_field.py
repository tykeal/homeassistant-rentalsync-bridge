# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""AvailableField model for dynamically discovered Cloudbeds fields."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.models.listing import Listing


def _utc_now() -> datetime:
    """Get current UTC datetime for SQLAlchemy defaults."""
    return datetime.now(UTC)


class AvailableField(Base):
    """Dynamically discovered field from Cloudbeds reservations.

    Fields are discovered during sync by inspecting reservation data.
    This allows users to configure any field present in their Cloudbeds
    data, including custom fields specific to their account.
    """

    __tablename__ = "available_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    field_key: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sample_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    # Relationships
    listing: Mapped["Listing"] = relationship(
        "Listing", back_populates="available_fields"
    )

    __table_args__ = (
        UniqueConstraint(
            "listing_id", "field_key", name="uq_availablefield_listing_key"
        ),
        Index("idx_availablefield_listing", "listing_id"),
    )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"<AvailableField(id={self.id}, field_key={self.field_key}, "
            f"display_name={self.display_name})>"
        )
