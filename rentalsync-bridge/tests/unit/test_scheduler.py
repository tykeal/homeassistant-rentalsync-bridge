# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for sync scheduler."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from src.models.system_settings import DEFAULT_SYNC_INTERVAL_MINUTES
from src.services.scheduler import (
    MAX_SYNC_INTERVAL_MINUTES,
    MIN_SYNC_INTERVAL_MINUTES,
    SyncScheduler,
    get_scheduler,
    init_scheduler,
)


class TestSyncScheduler:
    """Tests for SyncScheduler."""

    @pytest.fixture
    def mock_session_factory(self):
        """Create mock session factory."""
        factory = MagicMock(spec=async_sessionmaker)
        # Create mock session with context manager support
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        factory.return_value = mock_cm
        return factory

    @pytest.mark.asyncio
    async def test_start_scheduler(self, mock_session_factory):
        """Test starting the scheduler."""
        scheduler = SyncScheduler(mock_session_factory)

        with (
            patch.object(scheduler._scheduler, "add_job") as mock_add_job,
            patch.object(scheduler._scheduler, "start") as mock_start,
        ):
            await scheduler.start()

        # Two jobs: sync and purge
        assert mock_add_job.call_count == 2
        mock_start.assert_called_once()
        assert scheduler.is_running is True

    @pytest.mark.asyncio
    async def test_start_schedules_purge_job(self, mock_session_factory):
        """Test that starting scheduler adds purge job at 02:00 UTC."""
        scheduler = SyncScheduler(mock_session_factory)

        with (
            patch.object(scheduler._scheduler, "add_job") as mock_add_job,
            patch.object(scheduler._scheduler, "start"),
        ):
            await scheduler.start()

        # Verify purge job was added
        job_ids = [call.kwargs.get("id") for call in mock_add_job.call_args_list]
        assert "purge_old_bookings" in job_ids

    @pytest.mark.asyncio
    async def test_start_already_running(self, mock_session_factory):
        """Test starting already running scheduler logs warning."""
        scheduler = SyncScheduler(mock_session_factory)
        scheduler._running = True

        with patch.object(scheduler._scheduler, "add_job") as mock_add_job:
            await scheduler.start()

        mock_add_job.assert_not_called()

    def test_stop_scheduler(self, mock_session_factory):
        """Test stopping the scheduler."""
        scheduler = SyncScheduler(mock_session_factory)
        scheduler._running = True

        with patch.object(scheduler._scheduler, "shutdown") as mock_shutdown:
            scheduler.stop()

        mock_shutdown.assert_called_once_with(wait=False)
        assert scheduler.is_running is False

    def test_stop_not_running(self, mock_session_factory):
        """Test stopping non-running scheduler does nothing."""
        scheduler = SyncScheduler(mock_session_factory)

        with patch.object(scheduler._scheduler, "shutdown") as mock_shutdown:
            scheduler.stop()

        mock_shutdown.assert_not_called()

    def test_is_running_property(self, mock_session_factory):
        """Test is_running property."""
        scheduler = SyncScheduler(mock_session_factory)

        assert scheduler.is_running is False
        scheduler._running = True
        assert scheduler.is_running is True

    def test_current_interval_minutes_property(self, mock_session_factory):
        """Test current_interval_minutes property."""
        scheduler = SyncScheduler(mock_session_factory)

        assert scheduler.current_interval_minutes == DEFAULT_SYNC_INTERVAL_MINUTES
        scheduler._current_interval_minutes = 10
        assert scheduler.current_interval_minutes == 10

    def test_update_sync_interval(self, mock_session_factory):
        """Test updating sync interval dynamically."""
        scheduler = SyncScheduler(mock_session_factory)
        scheduler._running = True

        with patch.object(scheduler._scheduler, "reschedule_job") as mock_reschedule:
            scheduler.update_sync_interval(10)

        mock_reschedule.assert_called_once()
        assert scheduler.current_interval_minutes == 10

    def test_update_sync_interval_not_running(self, mock_session_factory):
        """Test updating interval when scheduler not running."""
        scheduler = SyncScheduler(mock_session_factory)

        with patch.object(scheduler._scheduler, "reschedule_job") as mock_reschedule:
            scheduler.update_sync_interval(10)

        mock_reschedule.assert_not_called()

    def test_update_sync_interval_invalid_value(self, mock_session_factory):
        """Test updating interval with invalid values."""
        scheduler = SyncScheduler(mock_session_factory)
        scheduler._running = True

        with patch.object(scheduler._scheduler, "reschedule_job") as mock_reschedule:
            scheduler.update_sync_interval(MIN_SYNC_INTERVAL_MINUTES - 1)  # Too low
            scheduler.update_sync_interval(MAX_SYNC_INTERVAL_MINUTES + 1)  # Too high

        mock_reschedule.assert_not_called()

    def test_update_sync_interval_unchanged(self, mock_session_factory):
        """Test updating interval with same value does nothing."""
        scheduler = SyncScheduler(mock_session_factory)
        scheduler._running = True
        scheduler._current_interval_minutes = 5

        with patch.object(scheduler._scheduler, "reschedule_job") as mock_reschedule:
            scheduler.update_sync_interval(5)

        mock_reschedule.assert_not_called()


class TestSchedulerGlobals:
    """Tests for global scheduler functions."""

    def test_get_scheduler_returns_none_initially(self):
        """Test get_scheduler returns None when not initialized."""
        import src.services.scheduler as scheduler_module

        scheduler_module._scheduler = None

        result = get_scheduler()
        assert result is None

    def test_init_scheduler(self):
        """Test init_scheduler creates and returns scheduler."""
        mock_factory = MagicMock(spec=async_sessionmaker)

        scheduler = init_scheduler(mock_factory)

        assert scheduler is not None
        assert isinstance(scheduler, SyncScheduler)

    def test_init_scheduler_with_cache(self):
        """Test init_scheduler with calendar cache."""
        from src.services.calendar_service import CalendarCache

        mock_factory = MagicMock(spec=async_sessionmaker)
        cache = CalendarCache()

        scheduler = init_scheduler(mock_factory, cache)

        assert scheduler._calendar_cache is cache


class TestPurgeJob:
    """Tests for the data purge job."""

    @pytest.fixture
    def mock_session_factory(self):
        """Create mock session factory."""
        return MagicMock(spec=async_sessionmaker)

    @pytest.mark.asyncio
    async def test_purge_old_bookings_calls_repository(self, mock_session_factory):
        """Test purge job calls both repository methods."""
        from unittest.mock import AsyncMock

        # Create mock session with context manager support
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        # Create mock context manager
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session_factory.return_value = mock_cm

        scheduler = SyncScheduler(mock_session_factory)

        with patch(
            "src.services.scheduler.BookingRepository"
        ) as mock_booking_repo_class:
            mock_repo = MagicMock()
            mock_repo.purge_old_bookings = AsyncMock(return_value=5)
            mock_repo.purge_cancelled_bookings = AsyncMock(return_value=3)
            mock_booking_repo_class.return_value = mock_repo

            await scheduler._purge_old_bookings()

            mock_repo.purge_old_bookings.assert_called_once_with(days=90)
            mock_repo.purge_cancelled_bookings.assert_called_once_with(days=30)
