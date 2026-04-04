# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Performance and benchmark integration tests.

Verifies deterministic token behaviour and measures sync-cycle and iCal
generation latency.  Tests marked ``slow`` are skipped by default;
run with ``pytest -m slow`` to include them.
"""

import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base
from src.models.oauth_credential import OAuthCredential
from src.providers.base import PMSReservation, TokenRateLimitError
from src.providers.guesty.auth import TOKEN_REQUEST_LIMIT, GuestyTokenManager
from src.repositories.credential_repository import CredentialRepository
from src.services.calendar_service import CalendarCache, CalendarService
from src.services.sync_service import SyncService

from tests.conftest import make_booking, make_listing


@pytest.fixture
async def db_engine():
    """Create async in-memory SQLite engine."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _rec):
        """Enable SQLite FK constraints."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    """Create async session factory bound to test engine."""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(session_factory) -> AsyncGenerator[AsyncSession]:
    """Yield an async database session for each test."""
    async with session_factory() as s:
        yield s


class TestTokenRequestDeterminism:
    """Token requests stay within the 5/day budget."""

    async def test_token_requests_at_most_two_per_cycle(self, session):
        """Simulated 24h cycle should use <= 2 token requests.

        One request for the initial token, possibly one more if the
        token expires (24h validity by default).  The cache should
        prevent additional requests.
        """
        cred = OAuthCredential(
            pms_type="guesty",
            client_id="perf_cid",
            client_secret="perf_csec",
        )
        session.add(cred)
        await session.commit()
        await session.refresh(cred)

        repo = CredentialRepository(session)

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(
            return_value=AsyncMock(
                status_code=200,
                json=lambda: {
                    "access_token": "tok_perf",
                    "expires_in": 86400,
                },
                text="ok",
            )
        )

        tm = GuestyTokenManager(
            client_id="perf_cid",
            client_secret="perf_csec",
            credential_repo=repo,
            credential_id=cred.id,
            http_client=mock_http,
        )

        # First call: should request a new token
        token1 = await tm.get_token()
        assert token1 == "tok_perf"

        # Subsequent calls within validity: cached
        for _ in range(10):
            tok = await tm.get_token()
            assert tok == "tok_perf"

        # Total HTTP requests should be exactly 1 (cache hit on rest)
        assert mock_http.post.call_count == 1

        count = await repo.get_token_request_count(cred.id)
        assert count <= 2

    async def test_rate_limit_prevents_excess(self, session):
        """After 5 requests in a window, a 6th raises error."""
        cred = OAuthCredential(
            pms_type="guesty",
            client_id="rl_cid",
            client_secret="rl_csec",
        )
        session.add(cred)
        await session.commit()
        await session.refresh(cred)

        repo = CredentialRepository(session)

        # Simulate 5 increments
        for _ in range(TOKEN_REQUEST_LIMIT):
            await repo.increment_token_request_count(cred.id)

        tm = GuestyTokenManager(
            client_id="rl_cid",
            client_secret="rl_csec",
            credential_repo=repo,
            credential_id=cred.id,
        )
        # Clear cache to force rate check
        tm._cached_token = None
        tm._cached_expires_at = None

        with pytest.raises(TokenRateLimitError):
            await tm.get_token()


@pytest.mark.slow
class TestSyncCycleBenchmark:
    """Measure sync-cycle duration (informational, not gated)."""

    async def test_sync_cycle_latency(self, session, session_factory):
        """Test sync cycle completes within acceptable latency."""
        listing = make_listing(
            pms_id="bench_prop",
            name="Benchmark Property",
            slug="bench-prop",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        now = datetime.now(UTC)
        reservations = [
            PMSReservation(
                pms_booking_id=f"BENCH_BK{i:04d}",
                listing_pms_id="bench_prop",
                guest_name=f"Bench Guest {i}",
                guest_id=None,
                check_in=now + timedelta(days=i),
                check_out=now + timedelta(days=i + 3),
                status="confirmed",
                room_ids=(),
                custom_data={},
            )
            for i in range(50)
        ]

        mock_provider = AsyncMock()
        mock_provider.provider_type = "guesty"
        mock_provider.get_reservations = AsyncMock(return_value=reservations)
        mock_provider.get_guest = AsyncMock(return_value=None)

        sync = SyncService(
            session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=session_factory,
        )

        start = time.monotonic()
        counts = await sync.sync_listing(listing, mock_provider)
        elapsed = time.monotonic() - start

        assert counts["inserted"] == 50
        # Informational — not a hard gate
        assert elapsed < 30.0, f"Sync took {elapsed:.2f}s (expected <30s)"


@pytest.mark.slow
class TestICalGenerationBenchmark:
    """Measure iCal feed generation latency (informational)."""

    async def test_ical_generation_latency(self, session):
        """Test iCal generation completes within acceptable latency."""
        listing = make_listing(
            pms_id="ical_bench",
            name="iCal Bench Property",
            slug="ical-bench",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        now = datetime.now(UTC)
        bookings = []
        for i in range(100):
            bk = make_booking(
                listing_id=listing.id,
                pms_booking_id=f"ICAL_BK{i:04d}",
                guest_name=f"iCal Guest {i}",
                check_in_date=now + timedelta(days=i),
                check_out_date=now + timedelta(days=i + 3),
            )
            session.add(bk)
            bookings.append(bk)
        await session.commit()

        cal_service = CalendarService(cache=CalendarCache(ttl_seconds=0))

        start = time.monotonic()
        ical_str = cal_service.generate_ical(listing, bookings)
        elapsed = time.monotonic() - start

        assert "BEGIN:VCALENDAR" in ical_str
        assert ical_str.count("BEGIN:VEVENT") == 100
        # Informational — not a hard gate
        assert elapsed < 5.0, f"iCal gen took {elapsed:.2f}s (expected <5s)"
