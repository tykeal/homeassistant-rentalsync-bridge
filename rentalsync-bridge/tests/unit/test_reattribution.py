# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for re-anchoring re-attributed stays."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from src.services.reattribution import reanchor

LA = ZoneInfo("America/Los_Angeles")


class TestReanchor:
    """Stored instants move to the by-IDs local date."""

    @pytest.mark.parametrize(
        "stored",
        [datetime(2026, 11, 14), datetime(2026, 11, 14, tzinfo=UTC)],
    )
    def test_unchanged_all_day_date_is_kept(self, stored: datetime) -> None:
        """A midnight-UTC date west of UTC is the date it names."""
        assert reanchor(stored, date(2026, 11, 14), LA) is stored

    def test_moved_all_day_date_stays_midnight_utc(self) -> None:
        """A moved all-day date is still an all-day date."""
        stored = datetime(2026, 11, 14, tzinfo=UTC)

        assert reanchor(stored, date(2026, 11, 16), LA) == datetime(
            2026, 11, 16, tzinfo=UTC
        )

    def test_moved_timed_instant_keeps_local_time(self) -> None:
        """A timed stay keeps its local time of day on the new date."""
        stored = datetime(2026, 11, 14, 23, 0, tzinfo=UTC)  # 15:00 local

        assert reanchor(stored, date(2026, 11, 16), LA) == datetime(
            2026, 11, 16, 23, 0, tzinfo=UTC
        )
