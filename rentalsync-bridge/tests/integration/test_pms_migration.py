# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the PMS column generalization migration."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

# Resolve alembic.ini relative to rentalsync-bridge directory
_RENTALSYNC_DIR = Path(__file__).parent.parent.parent
_ALEMBIC_INI = str(_RENTALSYNC_DIR / "alembic.ini")


def _run_migrations(engine, target_revision):
    """Run Alembic migrations up to target_revision on engine."""
    cfg = Config(_ALEMBIC_INI)
    # Override script_location to absolute path so tests work from any cwd
    cfg.set_main_option("script_location", str(_RENTALSYNC_DIR / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    with engine.connect() as conn:
        # Ensure alembic_version table exists
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL)"
            )
        )
        conn.commit()

        # Get current revision
        result = conn.execute(text("SELECT version_num FROM alembic_version"))
        row = result.fetchone()
        current = row[0] if row else None

        # Walk the revision chain
        revisions = list(script.iterate_revisions(target_revision, current or "base"))

        for rev in reversed(revisions):
            # Create migration context and run the upgrade
            context = MigrationContext.configure(conn)
            with Operations.context(context):
                rev.module.upgrade()  # type: ignore[union-attr]

            # Stamp the version
            if current is None:
                conn.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:ver)"),
                    {"ver": rev.revision},
                )
            else:
                conn.execute(
                    text("UPDATE alembic_version SET version_num = :ver"),
                    {"ver": rev.revision},
                )
            current = rev.revision
        conn.commit()


@pytest.fixture
def pre_migration_db():
    """Migrate to the revision just before PMS generalisation."""
    engine = create_engine("sqlite:///:memory:")
    _run_migrations(engine, "c2d3e4f5a6b7")
    yield engine
    engine.dispose()


class TestPmsMigration:
    """Tests for d3e4f5a6b7c8 - PMS column generalisation."""

    def test_upgrade_renames_listing_column(self, pre_migration_db):
        """Verify cloudbeds_id is renamed to pms_id on listings."""
        engine = pre_migration_db

        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO listings "
                    "(cloudbeds_id, name, enabled, ical_url_slug,"
                    " timezone, sync_enabled, created_at, updated_at)"
                    " VALUES ('PROP1', 'Beach House', 1, 'beach',"
                    " 'UTC', 1, '2026-01-01', '2026-01-01')"
                )
            )
            conn.commit()

        _run_migrations(engine, "d3e4f5a6b7c8")

        with engine.connect() as conn:
            result = conn.execute(text("SELECT pms_id, name FROM listings"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == "PROP1"
            assert row[1] == "Beach House"

        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("listings")}
        assert "pms_id" in columns
        assert "cloudbeds_id" not in columns

    def test_upgrade_renames_booking_column(self, pre_migration_db):
        """Verify cloudbeds_booking_id renamed to pms_booking_id."""
        engine = pre_migration_db

        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO listings "
                    "(cloudbeds_id, name, enabled, ical_url_slug,"
                    " timezone, sync_enabled, created_at, updated_at)"
                    " VALUES ('PROP1', 'Test', 1, 'test',"
                    " 'UTC', 1, '2026-01-01', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO bookings "
                    "(listing_id, cloudbeds_booking_id, guest_name,"
                    " check_in_date, check_out_date, status,"
                    " last_fetched_at, created_at, updated_at) "
                    "VALUES (1, 'BK123', 'Alice',"
                    " '2026-02-01', '2026-02-05', 'confirmed',"
                    " '2026-01-01', '2026-01-01', '2026-01-01')"
                )
            )
            conn.commit()

        _run_migrations(engine, "d3e4f5a6b7c8")

        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT pms_booking_id, guest_name FROM bookings")
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] == "BK123"
            assert row[1] == "Alice"

        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("bookings")}
        assert "pms_booking_id" in columns
        assert "cloudbeds_booking_id" not in columns

    def test_upgrade_renames_room_column(self, pre_migration_db):
        """Verify cloudbeds_room_id is renamed to pms_room_id."""
        engine = pre_migration_db

        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO listings "
                    "(cloudbeds_id, name, enabled, ical_url_slug,"
                    " timezone, sync_enabled, created_at, updated_at)"
                    " VALUES ('PROP1', 'Test', 1, 'test',"
                    " 'UTC', 1, '2026-01-01', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO rooms "
                    "(listing_id, cloudbeds_room_id, room_name,"
                    " ical_url_slug, enabled, created_at, updated_at)"
                    " VALUES (1, 'ROOM1', 'Suite A',"
                    " 'suite-a', 1, '2026-01-01', '2026-01-01')"
                )
            )
            conn.commit()

        _run_migrations(engine, "d3e4f5a6b7c8")

        with engine.connect() as conn:
            result = conn.execute(text("SELECT pms_room_id, room_name FROM rooms"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == "ROOM1"
            assert row[1] == "Suite A"

        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("rooms")}
        assert "pms_room_id" in columns
        assert "cloudbeds_room_id" not in columns

    def test_upgrade_adds_oauth_columns(self, pre_migration_db):
        """Verify new oauth_credentials columns with defaults."""
        engine = pre_migration_db

        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO oauth_credentials "
                    "(client_id, client_secret,"
                    " created_at, updated_at) VALUES "
                    "('cid', 'csec', '2026-01-01', '2026-01-01')"
                )
            )
            conn.commit()

        _run_migrations(engine, "d3e4f5a6b7c8")

        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT pms_type, token_request_count,"
                    " token_request_window_start"
                    " FROM oauth_credentials"
                )
            )
            row = result.fetchone()
            assert row is not None
            assert row[0] == "cloudbeds"
            assert row[1] == 0
            assert row[2] is None

        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("oauth_credentials")}
        assert "pms_type" in columns
        assert "token_request_count" in columns
        assert "token_request_window_start" in columns

    def test_upgrade_booking_unique_constraint(self, pre_migration_db):
        """Verify unique constraint on (listing_id, pms_booking_id)."""
        engine = pre_migration_db

        # Insert valid data first
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO listings "
                    "(cloudbeds_id, name, enabled, ical_url_slug,"
                    " timezone, sync_enabled, created_at, updated_at)"
                    " VALUES ('PROP1', 'Test', 1, 'test',"
                    " 'UTC', 1, '2026-01-01', '2026-01-01')"
                )
            )
            conn.commit()

        _run_migrations(engine, "d3e4f5a6b7c8")

        # Insert a booking via pms_booking_id
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO bookings "
                    "(listing_id, pms_booking_id, guest_name,"
                    " check_in_date, check_out_date, status,"
                    " last_fetched_at, created_at, updated_at) "
                    "VALUES (1, 'BK1', 'Alice',"
                    " '2026-02-01', '2026-02-05', 'confirmed',"
                    " '2026-01-01', '2026-01-01', '2026-01-01')"
                )
            )
            conn.commit()

        # Duplicate pms_booking_id for same listing should fail
        with (
            pytest.raises(
                IntegrityError,
                match="UNIQUE constraint failed",
            ),
            engine.connect() as conn,
        ):
            conn.execute(
                text(
                    "INSERT INTO bookings "
                    "(listing_id, pms_booking_id, guest_name,"
                    " check_in_date, check_out_date, status,"
                    " last_fetched_at, created_at, updated_at)"
                    " VALUES (1, 'BK1', 'Bob',"
                    " '2026-03-01', '2026-03-05', 'confirmed',"
                    " '2026-01-01', '2026-01-01',"
                    " '2026-01-01')"
                )
            )
            conn.commit()

    def test_upgrade_room_unique_constraint(self, pre_migration_db):
        """Verify unique constraint on (listing_id, pms_room_id)."""
        engine = pre_migration_db

        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO listings "
                    "(cloudbeds_id, name, enabled, ical_url_slug,"
                    " timezone, sync_enabled, created_at, updated_at)"
                    " VALUES ('PROP1', 'Test', 1, 'test',"
                    " 'UTC', 1, '2026-01-01', '2026-01-01')"
                )
            )
            conn.commit()

        _run_migrations(engine, "d3e4f5a6b7c8")

        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO rooms "
                    "(listing_id, pms_room_id, room_name,"
                    " ical_url_slug, enabled, created_at, updated_at)"
                    " VALUES (1, 'ROOM1', 'Suite A',"
                    " 'suite-a', 1, '2026-01-01', '2026-01-01')"
                )
            )
            conn.commit()

        # Duplicate pms_room_id for same listing should fail
        with (
            pytest.raises(
                IntegrityError,
                match="UNIQUE constraint failed",
            ),
            engine.connect() as conn,
        ):
            conn.execute(
                text(
                    "INSERT INTO rooms "
                    "(listing_id, pms_room_id, room_name,"
                    " ical_url_slug, enabled,"
                    " created_at, updated_at)"
                    " VALUES (1, 'ROOM1', 'Suite B',"
                    " 'suite-b', 1,"
                    " '2026-01-01', '2026-01-01')"
                )
            )
            conn.commit()

    def test_downgrade_raises_not_implemented(self, pre_migration_db):
        """Verify downgrade raises NotImplementedError."""
        _run_migrations(pre_migration_db, "d3e4f5a6b7c8")

        cfg = Config(_ALEMBIC_INI)
        cfg.set_main_option("script_location", str(_RENTALSYNC_DIR / "alembic"))
        script = ScriptDirectory.from_config(cfg)
        rev = script.get_revision("d3e4f5a6b7c8")

        with pytest.raises(NotImplementedError):
            rev.module.downgrade()  # type: ignore[union-attr]


class TestCrossListingDuplicatePurge:
    """Tests for f5a6b7c8d9e0 - purging cross-listing duplicate bookings."""

    @staticmethod
    def _seed(engine, bookings):
        """Seed two listings, a room each, and the given booking rows.

        Args:
            engine: SQLAlchemy engine migrated to ``e4f5a6b7c8d9``.
            bookings: Tuples of ``(listing_id, room_id, pms_booking_id,
                status)``.
        """
        stamp = "'2026-01-01', '2026-01-01'"
        with engine.connect() as conn:
            for pms_id, slug in (("P1", "p1"), ("P2", "p2")):
                conn.execute(
                    text(
                        "INSERT INTO listings "
                        "(pms_id, name, enabled, ical_url_slug, timezone,"
                        " sync_enabled, created_at, updated_at)"
                        f" VALUES ('{pms_id}', '{pms_id}', 1, '{slug}',"
                        f" 'UTC', 1, {stamp})"
                    )
                )
            for listing_id, room in ((1, "R1"), (1, "R1B"), (2, "R2")):
                conn.execute(
                    text(
                        "INSERT INTO rooms "
                        "(listing_id, pms_room_id, room_name, ical_url_slug,"
                        " enabled, created_at, updated_at)"
                        f" VALUES ({listing_id}, '{room}', '{room}',"
                        f" '{room.lower()}', 1, {stamp})"
                    )
                )
            for listing_id, room_id, booking_id, status in bookings:
                conn.execute(
                    text(
                        "INSERT INTO bookings "
                        "(listing_id, room_id, pms_booking_id, guest_name,"
                        " check_in_date, check_out_date, status,"
                        " created_at, updated_at, last_fetched_at)"
                        f" VALUES ({listing_id}, {room_id or 'NULL'},"
                        f" '{booking_id}', 'A', '2026-03-01', '2026-03-05',"
                        f" '{status}', {stamp}, '2026-01-01')"
                    )
                )
            conn.commit()

    @staticmethod
    def _surviving(engine):
        """Return the surviving ``(listing_id, pms_booking_id)`` rows.

        Args:
            engine: Migrated SQLAlchemy engine.

        Returns:
            Sorted list of tuples.
        """
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT listing_id, pms_booking_id FROM bookings"
                    " ORDER BY listing_id, pms_booking_id"
                )
            ).fetchall()
        return [tuple(r) for r in rows]

    def test_purges_only_foreign_duplicates(self, pre_migration_db):
        """Verify duplicated room-less rows go and owned rows stay.

        ``BK1`` is owned by listing 1, which currently holds it against a
        room, and was duplicated onto listing 2.  ``SOLO`` exists only on
        listing 2 with no room, so nothing proves it foreign.
        """
        engine = pre_migration_db
        _run_migrations(engine, "e4f5a6b7c8d9")
        self._seed(
            engine,
            (
                (1, 1, "BK1::R1", "confirmed"),
                (2, None, "BK1::R1", "confirmed"),
                (2, None, "SOLO", "confirmed"),
            ),
        )

        _run_migrations(engine, "f5a6b7c8d9e0")

        assert self._surviving(engine) == [(1, "BK1::R1"), (2, "SOLO")]

    def test_assigned_to_unassigned_row_survives(self, pre_migration_db):
        """Verify an unassigned owner row is not purged (regression).

        After a multi-unit stay is unassigned, its owner keeps a cancelled
        ``BK1::R1`` row carrying the old room next to the live, room-less
        ``BK1`` row.  A historical assignment is not current ownership, so
        the live row must survive even though another listing holds a stale
        foreign copy.
        """
        engine = pre_migration_db
        _run_migrations(engine, "e4f5a6b7c8d9")
        self._seed(
            engine,
            (
                (1, 1, "BK1::R1", "cancelled"),
                (1, None, "BK1", "confirmed"),
                (2, None, "BK1", "confirmed"),
            ),
        )

        _run_migrations(engine, "f5a6b7c8d9e0")

        assert self._surviving(engine) == [
            (1, "BK1"),
            (1, "BK1::R1"),
            (2, "BK1"),
        ]

    def test_unassigned_owner_row_survives(self, pre_migration_db):
        """Verify a legitimately unassigned booking is preserved.

        No listing currently holds the reservation against a room, so there
        is no evidence of ownership and every ambiguous row is kept.
        """
        engine = pre_migration_db
        _run_migrations(engine, "e4f5a6b7c8d9")
        self._seed(
            engine,
            (
                (1, None, "BK1", "confirmed"),
                (2, None, "BK1", "confirmed"),
            ),
        )

        _run_migrations(engine, "f5a6b7c8d9e0")

        assert self._surviving(engine) == [(1, "BK1"), (2, "BK1")]

    def test_reassigned_row_on_owning_listing_survives(self, pre_migration_db):
        """Verify a room-less row is kept when its listing holds the room.

        A reassignment can leave a stale room-less row beside the live
        assigned row on the owning listing; deleting it on the strength of
        another listing's assignment would risk removing real data.
        """
        engine = pre_migration_db
        _run_migrations(engine, "e4f5a6b7c8d9")
        self._seed(
            engine,
            (
                (1, 2, "BK1::R1B", "confirmed"),
                (1, None, "BK1", "confirmed"),
                (2, 3, "BK1::R2", "confirmed"),
            ),
        )

        _run_migrations(engine, "f5a6b7c8d9e0")

        assert self._surviving(engine) == [
            (1, "BK1"),
            (1, "BK1::R1B"),
            (2, "BK1::R2"),
        ]

    def test_downgrade_raises_not_implemented(self):
        """Verify downgrade raises NotImplementedError."""
        cfg = Config(_ALEMBIC_INI)
        cfg.set_main_option("script_location", str(_RENTALSYNC_DIR / "alembic"))
        script = ScriptDirectory.from_config(cfg)
        rev = script.get_revision("f5a6b7c8d9e0")

        with pytest.raises(NotImplementedError):
            rev.module.downgrade()  # type: ignore[union-attr]
