# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Datetime helpers shared across RentalSync Bridge."""

from datetime import UTC, datetime


def ensure_utc(value: datetime) -> datetime:
    """Return *value* as a timezone-aware UTC datetime.

    Naive datetimes are treated as already holding UTC, matching how the rest
    of the codebase interprets stored and inbound naive timestamps.  Aware
    datetimes are converted to UTC.

    Args:
        value: Naive or timezone-aware datetime.

    Returns:
        Timezone-aware datetime in UTC.
    """
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
