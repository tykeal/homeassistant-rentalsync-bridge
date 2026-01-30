# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Booking model for cached Cloudbeds reservation data."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.models.listing import Listing


def _utc_now() -> datetime:
    """Get current UTC datetime for SQLAlchemy defaults."""
    return datetime.now(UTC)


class Booking(Base):
    """Cached booking data from Cloudbeds API.

    Bookings are cached locally for iCal generation and refreshed
    periodically via the sync service.
    """

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    cloudbeds_booking_id: Mapped[str] = mapped_column(String(100), nullable=False)
    guest_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guest_phone_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    check_in_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    check_out_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="confirmed")
    custom_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_fetched_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    # Relationships
    listing: Mapped["Listing"] = relationship("Listing", back_populates="bookings")

    __table_args__ = (
        UniqueConstraint(
            "listing_id", "cloudbeds_booking_id", name="uq_booking_listing_cloudbeds"
        ),
        Index("idx_booking_listing", "listing_id"),
        Index("idx_booking_dates", "listing_id", "check_in_date", "check_out_date"),
        Index("idx_booking_status", "status"),
    )

    @property
    def event_title(self) -> str:
        """Get event title for iCal (guest name or booking ID).

        Returns:
            Guest name if available, otherwise booking ID.
        """
        return self.guest_name or self.cloudbeds_booking_id

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"<Booking(id={self.id}, cloudbeds_id={self.cloudbeds_booking_id}, "
            f"status={self.status})>"
        )
