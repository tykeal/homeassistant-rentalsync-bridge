# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""CustomField model for configurable iCal event description fields."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.models.listing import Listing


class CustomField(Base):
    """Configurable field for iCal event descriptions.

    Each listing can have multiple custom fields that pull data from
    Cloudbeds bookings into the iCal event description.
    """

    __tablename__ = "custom_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_label: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    listing: Mapped["Listing"] = relationship("Listing", back_populates="custom_fields")

    __table_args__ = (
        UniqueConstraint(
            "listing_id", "field_name", name="uq_customfield_listing_field"
        ),
        Index("idx_customfield_listing", "listing_id"),
    )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"<CustomField(id={self.id}, field_name={self.field_name}, "
            f"enabled={self.enabled})>"
        )
