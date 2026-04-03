# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the PMS column generalization migration."""

import pytest
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def _run_migrations(engine, target_revision):
    """Run Alembic migrations up to target_revision on engine."""
    cfg = Config("alembic.ini")
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
                Exception,
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
                Exception,
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

        cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(cfg)
        rev = script.get_revision("d3e4f5a6b7c8")

        with pytest.raises(NotImplementedError):
            rev.module.downgrade()  # type: ignore[union-attr]
