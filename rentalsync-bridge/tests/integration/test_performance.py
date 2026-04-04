# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Performance and benchmark integration tests.

Verifies deterministic token behaviour and measures sync-cycle and iCal
generation latency.  Tests marked ``slow`` are skipped by default;
run with ``pytest -m slow`` to include them.
"""

import time
import warnings
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from src.models.oauth_credential import OAuthCredential
from src.providers.base import PMSReservation, TokenRateLimitError
from src.providers.guesty.auth import TOKEN_REQUEST_LIMIT, GuestyTokenManager
from src.repositories.credential_repository import CredentialRepository
from src.services.calendar_service import CalendarCache, CalendarService
from src.services.sync_service import SyncService

from tests.conftest import make_booking, make_listing


class TestTokenRequestDeterminism:
    """Token requests stay within the 5/day budget."""

    async def test_token_requests_at_most_two_per_cycle(self, async_session):
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
        async_session.add(cred)
        await async_session.commit()
        await async_session.refresh(cred)

        repo = CredentialRepository(async_session)

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

    async def test_rate_limit_prevents_excess(self, async_session):
        """After 5 requests in a window, a 6th raises error."""
        cred = OAuthCredential(
            pms_type="guesty",
            client_id="rl_cid",
            client_secret="rl_csec",
        )
        async_session.add(cred)
        await async_session.commit()
        await async_session.refresh(cred)

        repo = CredentialRepository(async_session)

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
        await tm.invalidate_cache()

        with pytest.raises(TokenRateLimitError):
            await tm.get_token()


@pytest.mark.slow
class TestSyncCycleBenchmark:
    """Measure sync-cycle duration (informational, not gated)."""

    async def test_sync_cycle_latency(self, async_session, async_session_factory):
        """Test sync cycle completes within acceptable latency."""
        listing = make_listing(
            pms_id="bench_prop",
            name="Benchmark Property",
            slug="bench-prop",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

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
            async_session,
            calendar_cache=CalendarCache(ttl_seconds=0),
            session_factory=async_session_factory,
        )

        start = time.monotonic()
        counts = await sync.sync_listing(listing, mock_provider)
        elapsed = time.monotonic() - start

        assert counts["inserted"] == 50
        # Informational — not a hard gate
        if elapsed > 30.0:
            warnings.warn(f"Sync took {elapsed:.2f}s (expected <30s)", stacklevel=1)


@pytest.mark.slow
class TestICalGenerationBenchmark:
    """Measure iCal feed generation latency (informational)."""

    async def test_ical_generation_latency(self, async_session):
        """Test iCal generation completes within acceptable latency."""
        listing = make_listing(
            pms_id="ical_bench",
            name="iCal Bench Property",
            slug="ical-bench",
        )
        async_session.add(listing)
        await async_session.commit()
        await async_session.refresh(listing)

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
            async_session.add(bk)
            bookings.append(bk)
        await async_session.commit()

        cal_service = CalendarService(cache=CalendarCache(ttl_seconds=0))

        start = time.monotonic()
        ical_str = cal_service.generate_ical(listing, bookings)
        elapsed = time.monotonic() - start

        assert "BEGIN:VCALENDAR" in ical_str
        assert ical_str.count("BEGIN:VEVENT") == 100
        # Informational — not a hard gate
        if elapsed > 5.0:
            warnings.warn(f"iCal gen took {elapsed:.2f}s (expected <5s)", stacklevel=1)
