# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for database configuration."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import Base, get_database_url


class TestGetDatabaseUrl:
    """Tests for get_database_url function."""

    def test_sqlite_url_conversion(self):
        """Test that sqlite:// is converted to sqlite+aiosqlite://."""
        url = get_database_url()
        assert "aiosqlite" in url or "sqlite+aiosqlite" in url

    def test_url_returns_string(self):
        """Test that URL is returned as string."""
        url = get_database_url()
        assert isinstance(url, str)


class TestBase:
    """Tests for declarative base."""

    def test_base_is_declarative_base(self):
        """Test that Base is a proper declarative base."""
        from sqlalchemy.orm import DeclarativeBase

        assert issubclass(Base, DeclarativeBase)


class TestAsyncSession:
    """Tests for async session functionality."""

    @pytest.mark.asyncio
    async def test_session_fixture_creates_session(self, async_session):
        """Test that async_session fixture provides a valid session."""
        assert isinstance(async_session, AsyncSession)
        assert async_session.is_active

    @pytest.mark.asyncio
    async def test_session_can_execute_query(self, async_session):
        """Test that session can execute basic queries."""
        from sqlalchemy import text

        result = await async_session.execute(text("SELECT 1"))
        row = result.scalar()
        assert row == 1
