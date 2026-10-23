# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the withheld bookings API endpoint."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base, get_db
from src.main import create_app
from src.models.booking import Booking
from src.models.listing import Listing
from src.repositories import booking_repository


@pytest.fixture
async def withheld_engine():
    """Create test database engine."""
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
async def withheld_session(withheld_engine) -> AsyncGenerator[AsyncSession]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        withheld_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def withheld_app(withheld_engine) -> AsyncGenerator:
    """Create test app with overridden DB dependency."""
    app = create_app()
    session_factory = async_sessionmaker(
        withheld_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()


async def _seed(session: AsyncSession) -> Listing:
    """Create an enabled listing with one withheld and one normal booking."""
    listing = Listing(
        pms_id="PROP1",
        name="Beach House",
        ical_url_slug="beach-house",
        enabled=True,
        sync_enabled=True,
    )
    session.add(listing)
    await session.commit()
    await session.refresh(listing)

    session.add_all(
        [
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_UNKNOWN",
                guest_name="Real Guest",
                guest_phone_last4="1234",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=4),
                status="Weird-Provider-Status",
            ),
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_EMPTY",
                guest_name="Another Guest",
                guest_phone_last4="5678",
                check_in_date=datetime.now(UTC) + timedelta(days=2),
                check_out_date=datetime.now(UTC) + timedelta(days=6),
                status="",
            ),
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_DECLINED",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=3),
                status="declined",
            ),
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_CONFIRMED",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=3),
                status="confirmed",
            ),
        ]
    )
    await session.commit()
    return listing


class TestWithheldBookings:
    """Tests for GET /api/bookings/withheld."""

    @pytest.mark.asyncio
    async def test_returns_raw_status_verbatim(self, withheld_app, withheld_session):
        """Raw provider statuses, including the empty one, survive intact."""
        listing = await _seed(withheld_session)

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/bookings/withheld",
                headers={"Authorization": "******"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        by_id = {b["pms_booking_id"]: b for b in data["bookings"]}
        assert set(by_id) == {"BK_UNKNOWN", "BK_EMPTY"}
        assert by_id["BK_UNKNOWN"]["status"] == "Weird-Provider-Status"
        assert by_id["BK_EMPTY"]["status"] == ""
        assert by_id["BK_UNKNOWN"]["listing_id"] == listing.id
        assert by_id["BK_UNKNOWN"]["listing_name"] == "Beach House"

    @pytest.mark.asyncio
    async def test_response_contains_no_guest_pii(self, withheld_app, withheld_session):
        """Privacy guarantee: no guest name or phone digits in the payload."""
        await _seed(withheld_session)

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/bookings/withheld",
                headers={"Authorization": "******"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["bookings"]
        for booking in data["bookings"]:
            assert "guest_name" not in booking
            assert "guest_phone_last4" not in booking
        body = response.text
        assert "Real Guest" not in body
        assert "1234" not in body

    @pytest.mark.asyncio
    async def test_excludes_disabled_listings(self, withheld_app, withheld_session):
        """Bookings on disabled listings are never exported, so not shown."""
        listing = Listing(
            pms_id="PROP2",
            name="Disabled House",
            ical_url_slug="disabled-house",
            enabled=False,
            sync_enabled=True,
        )
        withheld_session.add(listing)
        await withheld_session.commit()
        await withheld_session.refresh(listing)

        withheld_session.add(
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_HIDDEN",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=3),
                status="mystery",
            )
        )
        await withheld_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/bookings/withheld",
                headers={"Authorization": "******"},
            )

        assert response.status_code == 200
        assert response.json() == {"bookings": [], "count": 0}

    @pytest.mark.asyncio
    async def test_status_reports_withheld_count(self, withheld_app, withheld_session):
        """/api/status exposes a withheld count for the UI badge."""
        await _seed(withheld_session)

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/status",
                headers={"Authorization": "******"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["bookings_count"] == 4
        assert data["withheld_bookings_count"] == 2


class TestWithheldBookingDates:
    """Date serialisation must be unambiguous for the browser."""

    @pytest.mark.asyncio
    async def test_all_day_stay_serialised_as_plain_dates(
        self, withheld_app, withheld_session
    ):
        """All-day stays have no instant, so they carry no offset."""
        listing = Listing(
            pms_id="PROP_ALLDAY",
            name="All Day House",
            ical_url_slug="all-day-house",
            enabled=True,
            sync_enabled=True,
            timezone="America/Los_Angeles",
        )
        withheld_session.add(listing)
        await withheld_session.commit()
        await withheld_session.refresh(listing)

        withheld_session.add(
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_ALLDAY",
                check_in_date=datetime.now(UTC).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                + timedelta(days=1),
                check_out_date=datetime.now(UTC).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                + timedelta(days=4),
                status="mystery",
            )
        )
        await withheld_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/bookings/withheld",
                headers={"Authorization": "******"},
            )

        booking = response.json()["bookings"][0]
        assert booking["all_day"] is True
        assert booking["listing_timezone"] == "America/Los_Angeles"
        expected_in = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
        assert booking["check_in_date"] == expected_in
        assert "T" not in booking["check_in_date"]
        assert "T" not in booking["check_out_date"]

    @pytest.mark.asyncio
    async def test_timed_stay_serialised_in_listing_timezone(
        self, withheld_app, withheld_session
    ):
        """Timed stays carry an explicit listing-timezone UTC offset."""
        listing = Listing(
            pms_id="PROP_TIMED",
            name="Timed House",
            ical_url_slug="timed-house",
            enabled=True,
            sync_enabled=True,
            timezone="America/Los_Angeles",
        )
        withheld_session.add(listing)
        await withheld_session.commit()
        await withheld_session.refresh(listing)

        check_in = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1, hours=5)
        withheld_session.add(
            Booking(
                listing_id=listing.id,
                pms_booking_id="BK_TIMED",
                check_in_date=check_in,
                check_out_date=check_in + timedelta(days=3, hours=2),
                status="mystery",
            )
        )
        await withheld_session.commit()

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/bookings/withheld",
                headers={"Authorization": "******"},
            )

        booking = response.json()["bookings"][0]
        assert booking["all_day"] is False
        # Explicit offset, so the browser cannot reinterpret the instant.
        assert booking["check_in_date"].endswith(("-07:00", "-08:00"))
        assert datetime.fromisoformat(booking["check_in_date"]) == check_in


class TestWithheldListingLocalBoundary:
    """The panel and the badge must agree across a timezone boundary."""

    @pytest.fixture
    def frozen_now(self, monkeypatch):
        """Freeze the repository's clock just after a UTC date rollover.

        At 2024-06-10T01:00Z it is still 2024-06-09 in Los Angeles, so an
        all-day stay checking out on the 10th is still underway there.
        """
        frozen = datetime(2024, 6, 10, 1, 0, tzinfo=UTC)

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen if tz else frozen.replace(tzinfo=None)

        monkeypatch.setattr(booking_repository, "datetime", _Frozen)
        return frozen

    async def _seed_boundary(self, session: AsyncSession) -> None:
        """Seed one still-local-today stay and one genuinely past stay."""
        listing = Listing(
            pms_id="PROP_LA",
            name="LA House",
            ical_url_slug="la-house",
            enabled=True,
            sync_enabled=True,
            timezone="America/Los_Angeles",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        session.add_all(
            [
                Booking(
                    listing_id=listing.id,
                    pms_booking_id="BK_LOCAL_TODAY",
                    check_in_date=datetime(2024, 6, 7, tzinfo=UTC),
                    check_out_date=datetime(2024, 6, 10, tzinfo=UTC),
                    status="mystery",
                ),
                Booking(
                    listing_id=listing.id,
                    pms_booking_id="BK_PAST",
                    check_in_date=datetime(2024, 6, 5, tzinfo=UTC),
                    check_out_date=datetime(2024, 6, 9, tzinfo=UTC),
                    status="mystery",
                ),
            ]
        )
        await session.commit()

    @pytest.mark.asyncio
    async def test_badge_count_matches_panel_across_boundary(
        self, withheld_app, withheld_session, frozen_now
    ):
        """Badge and panel derive from one code path, so they agree."""
        await self._seed_boundary(withheld_session)

        async with AsyncClient(
            transport=ASGITransport(app=withheld_app), base_url="http://test"
        ) as client:
            panel = await client.get(
                "/api/bookings/withheld",
                headers={"Authorization": "******"},
            )
            status = await client.get(
                "/api/status",
                headers={"Authorization": "******"},
            )

        panel_data = panel.json()
        assert [b["pms_booking_id"] for b in panel_data["bookings"]] == [
            "BK_LOCAL_TODAY"
        ]
        assert status.json()["withheld_bookings_count"] == panel_data["count"]


class TestWithheldQueryCount:
    """The admin UI polls this, so it must not fan out queries."""

    @staticmethod
    async def _seed_listing(session: AsyncSession, suffix: str) -> Listing:
        """Create an enabled listing with one withheld booking."""
        listing = Listing(
            pms_id=f"PROP_Q{suffix}",
            name=f"Query House {suffix}",
            ical_url_slug=f"query-house-{suffix}",
            enabled=True,
            sync_enabled=True,
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)

        session.add(
            Booking(
                listing_id=listing.id,
                pms_booking_id=f"BK_Q{suffix}",
                check_in_date=datetime.now(UTC) + timedelta(days=1),
                check_out_date=datetime.now(UTC) + timedelta(days=3),
                status="mystery",
            )
        )
        await session.commit()
        return listing

    @staticmethod
    async def _add_bookings(
        session: AsyncSession, listing: Listing, count: int
    ) -> None:
        """Add extra withheld bookings to an existing listing."""
        session.add_all(
            [
                Booking(
                    listing_id=listing.id,
                    pms_booking_id=f"BK_EXTRA_{index}",
                    check_in_date=datetime.now(UTC) + timedelta(days=1),
                    check_out_date=datetime.now(UTC) + timedelta(days=3),
                    status="mystery",
                )
                for index in range(count)
            ]
        )
        await session.commit()

    @pytest.mark.asyncio
    async def test_single_query_and_no_relationship_loads(
        self, withheld_app, withheld_session, withheld_engine
    ):
        """One SELECT, and none of the eager relationships are touched."""
        listing = await self._seed_listing(withheld_session, "1")
        await self._seed_listing(withheld_session, "2")

        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            """Record every statement the engine actually executes."""
            statements.append(statement)

        event.listen(withheld_engine.sync_engine, "before_cursor_execute", record)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=withheld_app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/bookings/withheld",
                    headers={"Authorization": "******"},
                )
            baseline = list(statements)

            assert response.status_code == 200
            assert response.json()["count"] == 2

            selects = [s for s in baseline if s.lstrip().upper().startswith("SELECT")]
            assert len(selects) == 1, selects
            joined = " ".join(selects).lower()
            for relationship_table in ("rooms", "custom_fields", "available_fields"):
                assert relationship_table not in joined, selects

            # Adding more bookings to an existing listing must not add
            # queries - the per-listing loop would have done exactly that.
            await self._add_bookings(withheld_session, listing, 5)
            statements.clear()

            async with AsyncClient(
                transport=ASGITransport(app=withheld_app), base_url="http://test"
            ) as client:
                grown = await client.get(
                    "/api/bookings/withheld",
                    headers={"Authorization": "******"},
                )

            assert grown.json()["count"] == 7
            assert len(statements) == len(baseline), statements
        finally:
            event.remove(withheld_engine.sync_engine, "before_cursor_execute", record)
