# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for sync scheduler."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base
from src.models.listing import Listing
from src.models.oauth_credential import OAuthCredential
from src.models.system_settings import DEFAULT_SYNC_INTERVAL_MINUTES
from src.providers.base import PMSProviderError
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
    async def test_start_schedules_immediate_sync(self, mock_session_factory):
        """Test the sync job is scheduled to run immediately on startup."""
        scheduler = SyncScheduler(mock_session_factory)

        before = datetime.now(UTC)
        with (
            patch.object(scheduler._scheduler, "add_job") as mock_add_job,
            patch.object(scheduler._scheduler, "start"),
        ):
            await scheduler.start()
        after = datetime.now(UTC)

        sync_calls = [
            call
            for call in mock_add_job.call_args_list
            if call.kwargs.get("id") == "sync_all_listings"
        ]
        assert len(sync_calls) == 1
        next_run_time = sync_calls[0].kwargs["next_run_time"]
        assert before <= next_run_time <= after
        # The immediate run reuses the interval job, so max_instances=1
        # prevents it from overlapping a scheduled run.
        assert sync_calls[0].kwargs["max_instances"] == 1

    @pytest.mark.asyncio
    async def test_startup_sync_failure_does_not_propagate(self, mock_session_factory):
        """Test a failing startup sync does not break startup."""
        scheduler = SyncScheduler(mock_session_factory)

        with (
            patch.object(scheduler._scheduler, "add_job"),
            patch.object(scheduler._scheduler, "start"),
        ):
            await scheduler.start()

        assert scheduler.is_running is True

        session = mock_session_factory.return_value.__aenter__.return_value
        session.execute = AsyncMock(side_effect=RuntimeError("PMS unreachable"))

        # The job callable must swallow its own errors
        await scheduler._sync_all_listings()

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


class TestScheduledSyncStatusPersistence:
    """Tests that scheduled runs persist per-listing sync status."""

    @pytest.fixture
    async def engine(self):
        """Create an in-memory database engine with the schema applied."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=False,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
        await engine.dispose()

    @pytest.fixture
    def session_factory(self, engine):
        """Create a session factory bound to the test engine."""
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @pytest.fixture
    async def seeded_listings(self, session_factory):
        """Seed three sync-enabled listings and a usable credential."""
        async with session_factory() as session:
            credential = OAuthCredential(client_id="client", pms_type="guesty")
            credential.client_secret = "secret"
            credential.access_token = "token"
            credential.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
            session.add(credential)
            for index in range(1, 4):
                session.add(
                    Listing(
                        pms_id=f"PROP{index}",
                        name=f"Property {index}",
                        ical_url_slug=f"property-{index}",
                        enabled=True,
                        sync_enabled=True,
                    )
                )
            await session.commit()

    @staticmethod
    def _provider(reservations_by_listing=None, failing_pms_id=None):
        """Build a provider stub returning reservations per listing."""
        provider = AsyncMock()

        async def get_reservations(pms_id, *args, **kwargs):
            """Return the stubbed reservations, or fail for one listing."""
            if pms_id == failing_pms_id:
                raise PMSProviderError(f"boom for {pms_id}")
            return (reservations_by_listing or {}).get(pms_id, [])

        provider.get_reservations = AsyncMock(side_effect=get_reservations)
        return provider

    @staticmethod
    async def _sync_status(session_factory):
        """Return {pms_id: (last_sync_at, last_sync_error)} from a new session."""
        async with session_factory() as session:
            result = await session.execute(select(Listing))
            return {
                listing.pms_id: (listing.last_sync_at, listing.last_sync_error)
                for listing in result.scalars().all()
            }

    @pytest.mark.asyncio
    async def test_scheduled_sync_persists_status_for_every_listing(
        self, session_factory, seeded_listings
    ):
        """Every listing, including the last one, keeps its sync status."""
        scheduler = SyncScheduler(session_factory)

        with patch(
            "src.services.scheduler.create_provider_for_credential",
            return_value=self._provider(),
        ):
            await scheduler._sync_all_listings()

        status = await self._sync_status(session_factory)
        assert set(status) == {"PROP1", "PROP2", "PROP3"}
        for pms_id, (last_sync_at, last_sync_error) in status.items():
            assert last_sync_at is not None, f"{pms_id} lost its last_sync_at"
            assert last_sync_error is None

    @pytest.mark.asyncio
    async def test_scheduled_sync_records_error_without_clearing_it(
        self, session_factory, seeded_listings
    ):
        """A failing listing records its error; the others still succeed."""
        scheduler = SyncScheduler(session_factory)

        with patch(
            "src.services.scheduler.create_provider_for_credential",
            return_value=self._provider(failing_pms_id="PROP2"),
        ):
            await scheduler._sync_all_listings()

        status = await self._sync_status(session_factory)
        assert status["PROP2"][1] == "boom for PROP2"
        assert status["PROP1"][1] is None
        assert status["PROP3"][1] is None
        assert status["PROP1"][0] is not None
        assert status["PROP3"][0] is not None
