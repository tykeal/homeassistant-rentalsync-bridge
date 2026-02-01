# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Room model for individual units within a Cloudbeds property."""

from datetime import UTC, datetime
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
    from src.models.booking import Booking
    from src.models.listing import Listing


def _utc_now() -> datetime:
    """Get current UTC datetime for SQLAlchemy defaults."""
    return datetime.now(UTC)


class Room(Base):
    """Individual room/unit within a Cloudbeds property.

    Each room has its own iCal calendar URL for syncing to external platforms.
    Rooms are enabled by default and linked to a parent listing (property).
    """

    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    cloudbeds_room_id: Mapped[str] = mapped_column(String(100), nullable=False)
    room_name: Mapped[str] = mapped_column(String(255), nullable=False)
    room_type_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ical_url_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    # Relationships
    listing: Mapped["Listing"] = relationship("Listing", back_populates="rooms")
    bookings: Mapped[list["Booking"]] = relationship(
        "Booking",
        back_populates="room",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("listing_id", "ical_url_slug", name="uq_room_listing_slug"),
        UniqueConstraint(
            "listing_id", "cloudbeds_room_id", name="uq_room_listing_cloudbeds"
        ),
        Index("idx_room_listing", "listing_id"),
        Index("idx_room_enabled", "enabled"),
    )

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<Room(id={self.id}, name={self.room_name}, enabled={self.enabled})>"
