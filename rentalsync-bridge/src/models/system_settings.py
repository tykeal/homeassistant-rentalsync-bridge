# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""System settings model for runtime configuration stored in database."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base

# Default sync interval in minutes
DEFAULT_SYNC_INTERVAL_MINUTES = 5


class SystemSettings(Base):
    """System-wide settings stored in database.

    This is a single-row table that stores runtime configuration
    that can be modified without restarting the application.
    """

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    sync_interval_minutes: Mapped[int] = mapped_column(
        Integer,
        default=DEFAULT_SYNC_INTERVAL_MINUTES,
        nullable=False,
    )
    settings_key: Mapped[str] = mapped_column(
        String(50),
        default="default",
        unique=True,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<SystemSettings sync_interval={self.sync_interval_minutes}min>"
