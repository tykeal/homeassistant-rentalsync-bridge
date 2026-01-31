# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Background task scheduler for periodic sync operations."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.listing import Listing
from src.models.oauth_credential import OAuthCredential
from src.repositories.booking_repository import BookingRepository
from src.services.calendar_service import CalendarCache
from src.services.cloudbeds_service import CloudbedsService, CloudbedsServiceError
from src.services.sync_service import SYNC_INTERVAL_SECONDS, SyncService

# Data retention settings
OLD_BOOKING_RETENTION_DAYS = 90
CANCELLED_BOOKING_RETENTION_DAYS = 30

logger = logging.getLogger(__name__)


class SyncScheduler:
    """Scheduler for periodic booking sync tasks.

    Uses APScheduler to run background sync jobs at configured intervals.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        calendar_cache: CalendarCache | None = None,
    ) -> None:
        """Initialize scheduler.

        Args:
            session_factory: Factory for creating database sessions.
            calendar_cache: Optional calendar cache to invalidate on sync.
        """
        self._session_factory = session_factory
        self._calendar_cache = calendar_cache
        self._scheduler = AsyncIOScheduler()
        self._running = False

    def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._scheduler.add_job(
            self._sync_all_listings,
            trigger=IntervalTrigger(seconds=SYNC_INTERVAL_SECONDS),
            id="sync_all_listings",
            replace_existing=True,
            max_instances=1,  # Prevent overlapping syncs
        )

        # Add daily purge job at 02:00 UTC
        self._scheduler.add_job(
            self._purge_old_bookings,
            trigger=CronTrigger(hour=2, minute=0, timezone="UTC"),
            id="purge_old_bookings",
            replace_existing=True,
            max_instances=1,
        )

        self._scheduler.start()
        self._running = True
        logger.info(
            "Sync scheduler started with %d second interval", SYNC_INTERVAL_SECONDS
        )
        logger.info("Data purge scheduled daily at 02:00 UTC")

    def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return

        self._scheduler.shutdown(wait=False)
        self._running = False
        logger.info("Sync scheduler stopped")

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running.

        Returns:
            True if scheduler is running.
        """
        return self._running

    async def _sync_all_listings(self) -> None:
        """Sync all enabled listings."""
        logger.debug("Starting scheduled sync for all listings")

        async with self._session_factory() as session:
            try:
                # Get all sync-enabled listings
                result = await session.execute(
                    select(Listing).where(
                        Listing.enabled == True,  # noqa: E712
                        Listing.sync_enabled == True,  # noqa: E712
                    )
                )
                listings = result.scalars().all()

                if not listings:
                    logger.debug("No enabled listings to sync")
                    return

                # Get OAuth credentials
                cred_result = await session.execute(select(OAuthCredential).limit(1))
                credential = cred_result.scalar_one_or_none()

                if not credential:
                    logger.warning("No OAuth credentials configured, skipping sync")
                    return

                # Check token expiry
                if credential.is_token_expired():
                    logger.warning("OAuth token expired, attempting refresh")
                    await self._refresh_token(session, credential)

                # Sync each listing
                sync_service = SyncService(
                    session=session,
                    calendar_cache=self._calendar_cache,
                )

                total_inserted = 0
                total_updated = 0
                total_cancelled = 0

                for listing in listings:
                    try:
                        counts = await sync_service.sync_listing(listing, credential)
                        total_inserted += counts["inserted"]
                        total_updated += counts["updated"]
                        total_cancelled += counts["cancelled"]
                    except Exception:
                        logger.exception(
                            "Failed to sync listing %s", listing.cloudbeds_id
                        )
                        continue

                logger.info(
                    "Scheduled sync complete: %d inserted, %d updated, %d cancelled",
                    total_inserted,
                    total_updated,
                    total_cancelled,
                )

            except Exception:
                logger.exception("Error during scheduled sync")

    async def _refresh_token(
        self, session: AsyncSession, credential: OAuthCredential
    ) -> None:
        """Refresh OAuth token.

        Args:
            session: Database session.
            credential: OAuth credential to refresh.
        """
        try:
            cloudbeds = CloudbedsService(
                access_token=credential.access_token,
                refresh_token=credential.refresh_token,
            )

            new_access, new_refresh, expires_at = await cloudbeds.refresh_access_token()

            credential.access_token = new_access
            credential.refresh_token = new_refresh
            credential.token_expires_at = expires_at

            await session.commit()
            logger.info("OAuth token refreshed successfully")

        except CloudbedsServiceError as e:
            logger.error("Failed to refresh OAuth token: %s", e)
            raise

    async def _purge_old_bookings(self) -> None:
        """Purge old and cancelled bookings from the database.

        Runs daily at 02:00 UTC to clean up:
        - Bookings with checkout > 90 days ago
        - Cancelled bookings > 30 days old
        """
        logger.info("Starting scheduled data purge")

        async with self._session_factory() as session:
            try:
                booking_repo = BookingRepository(session)

                # Purge old bookings (90 days after checkout)
                old_count = await booking_repo.purge_old_bookings(
                    days=OLD_BOOKING_RETENTION_DAYS
                )

                # Purge cancelled bookings (30 days after cancellation)
                cancelled_count = await booking_repo.purge_cancelled_bookings(
                    days=CANCELLED_BOOKING_RETENTION_DAYS
                )

                await session.commit()

                logger.info(
                    "Data purge complete: %d old, %d cancelled bookings removed",
                    old_count,
                    cancelled_count,
                )

            except Exception:
                logger.exception("Error during data purge")


# Global scheduler instance
_scheduler: SyncScheduler | None = None


def get_scheduler() -> SyncScheduler | None:
    """Get the global scheduler instance.

    Returns:
        SyncScheduler instance or None if not initialized.
    """
    return _scheduler


def init_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    calendar_cache: CalendarCache | None = None,
) -> SyncScheduler:
    """Initialize the global scheduler.

    Args:
        session_factory: Factory for creating database sessions.
        calendar_cache: Optional calendar cache.

    Returns:
        Initialized SyncScheduler.
    """
    global _scheduler  # noqa: PLW0603
    _scheduler = SyncScheduler(session_factory, calendar_cache)
    return _scheduler
