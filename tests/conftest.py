# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Pytest fixtures for RentalSync Bridge tests."""

import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.database import Base


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment variables."""
    # Generate a valid Fernet key for testing
    test_key = Fernet.generate_key().decode()
    os.environ["ENCRYPTION_KEY"] = test_key
    os.environ["DATABASE_URL"] = "sqlite:///./test.db"
    os.environ["STANDALONE_MODE"] = "true"
    os.environ["CLOUDBEDS_CLIENT_ID"] = "test_client_id"
    os.environ["CLOUDBEDS_CLIENT_SECRET"] = "test_client_secret"
    os.environ["LOG_LEVEL"] = "DEBUG"
    yield
    # Cleanup
    test_db_path = Path("test.db")
    if test_db_path.exists():
        test_db_path.unlink()


@pytest.fixture
async def async_engine():
    """Create an async test database engine."""
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
async def async_session(async_engine) -> AsyncGenerator[AsyncSession]:
    """Create an async test database session."""
    session_factory = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def app() -> FastAPI:
    """Create a test FastAPI application."""
    from src.main import create_app

    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient]:
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sample_listing_data() -> dict[str, Any]:
    """Sample listing data for tests."""
    return {
        "cloudbeds_id": "test_property_123",
        "name": "Test Property",
        "enabled": True,
        "ical_url_slug": "test-property-123",
        "timezone": "America/New_York",
        "sync_enabled": True,
    }


@pytest.fixture
def sample_booking_data() -> dict[str, Any]:
    """Sample booking data for tests."""
    return {
        "cloudbeds_booking_id": "booking_456",
        "guest_name": "John Smith",
        "check_in_date": "2026-02-01",
        "check_out_date": "2026-02-05",
        "status": "confirmed",
        "phone_last4": "1234",
        "source": "direct",
    }


@pytest.fixture
def encryption_key() -> str:
    """Get test encryption key."""
    return os.environ["ENCRYPTION_KEY"]
