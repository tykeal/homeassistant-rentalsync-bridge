# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Listing model for Cloudbeds properties configured for iCal export."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.models.available_field import AvailableField
    from src.models.booking import Booking
    from src.models.custom_field import CustomField
    from src.models.room import Room


def _utc_now() -> datetime:
    """Get current UTC datetime for SQLAlchemy defaults."""
    return datetime.now(UTC)


class Listing(Base):
    """Cloudbeds property configured for iCal export.

    Each listing can have multiple bookings and custom field configurations.
    """

    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cloudbeds_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ical_url_slug: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")
    sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )

    # Relationships
    bookings: Mapped[list["Booking"]] = relationship(
        "Booking",
        back_populates="listing",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    custom_fields: Mapped[list["CustomField"]] = relationship(
        "CustomField",
        back_populates="listing",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    rooms: Mapped[list["Room"]] = relationship(
        "Room",
        back_populates="listing",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    available_fields: Mapped[list["AvailableField"]] = relationship(
        "AvailableField",
        back_populates="listing",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (Index("idx_listing_enabled", "enabled"),)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<Listing(id={self.id}, name={self.name}, enabled={self.enabled})>"
