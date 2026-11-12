# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for re-anchoring re-attributed stays."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from src.models.booking import Booking
from src.providers.base import (
    AbsenceResult,
    AbsenceVerdict,
    PMSOccupancyInterval,
    PMSReservation,
)
from src.services.reattribution import plan_reattributions, reanchor

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


class TestReattributedStayMustEndAfterItStarts:
    """A kept row is validated the way the calendar exports it."""

    @pytest.mark.asyncio
    async def test_mixed_row_on_one_exported_date_is_unconfirmed(self) -> None:
        """All-day check-in, timed check-out: one date means zero days."""
        rid = "RES1"
        booking = Booking(
            listing_id=1,
            pms_booking_id=f"{rid}::ROOM",
            guest_name="Synthetic Guest",
            check_in_date=datetime(2026, 11, 14, tzinfo=UTC),
            check_out_date=datetime(2026, 11, 14, 18, 0, tzinfo=UTC),
            status="confirmed",
        )
        day = date(2026, 11, 16)
        reservation = PMSReservation(
            pms_booking_id=rid,
            listing_pms_id="LST",
            guest_name=None,
            guest_id=None,
            check_in=datetime(2026, 11, 16, tzinfo=UTC),
            check_out=datetime(2026, 11, 16, tzinfo=UTC),
            status="confirmed",
            room_ids=("ROOM",),
            custom_data={},
            intervals=(PMSOccupancyInterval("ROOM", 1, None, None, (day, day), None),),
        )
        absence = {
            rid: AbsenceResult(AbsenceVerdict.REATTRIBUTED, reservation=reservation)
        }

        plans = await plan_reattributions(
            rows=[booking],
            absence=absence,
            tz=LA,
            normalise_status=lambda status, _rid: str(status),
            get_guest=None,  # type: ignore[arg-type]
            listing_pms_id="LST",
        )

        assert plans == {}
        assert absence[rid].verdict is AbsenceVerdict.UNCONFIRMED
