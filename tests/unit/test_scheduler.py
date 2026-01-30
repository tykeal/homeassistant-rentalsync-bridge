# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for sync scheduler."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from src.services.scheduler import (
    SyncScheduler,
    get_scheduler,
    init_scheduler,
)


class TestSyncScheduler:
    """Tests for SyncScheduler."""

    @pytest.fixture
    def mock_session_factory(self):
        """Create mock session factory."""
        return MagicMock(spec=async_sessionmaker)

    def test_start_scheduler(self, mock_session_factory):
        """Test starting the scheduler."""
        scheduler = SyncScheduler(mock_session_factory)

        with (
            patch.object(scheduler._scheduler, "add_job") as mock_add_job,
            patch.object(scheduler._scheduler, "start") as mock_start,
        ):
            scheduler.start()

        mock_add_job.assert_called_once()
        mock_start.assert_called_once()
        assert scheduler.is_running is True

    def test_start_already_running(self, mock_session_factory):
        """Test starting already running scheduler logs warning."""
        scheduler = SyncScheduler(mock_session_factory)
        scheduler._running = True

        with patch.object(scheduler._scheduler, "add_job") as mock_add_job:
            scheduler.start()

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
