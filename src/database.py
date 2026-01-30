# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Database configuration and async engine setup."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import get_settings


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all models."""

    pass


def get_database_url() -> str:
    """Get the database URL, converting sqlite to async driver.

    Returns:
        Database URL with async driver prefix.
    """
    settings = get_settings()
    url = settings.database_url

    # Convert sqlite:// to sqlite+aiosqlite://
    if url.startswith("sqlite://"):
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    return url


def create_engine() -> async_sessionmaker[AsyncSession]:
    """Create async database engine and session factory.

    Returns:
        Async session maker for database operations.
    """
    engine = create_async_engine(
        get_database_url(),
        echo=False,
        connect_args={"check_same_thread": False}
        if "sqlite" in get_database_url()
        else {},
    )

    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


# Global session factory - initialized on first use
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the global session factory.

    Returns:
        Async session maker for database operations.
    """
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = create_engine()
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession]:
    """Get an async database session.

    Yields:
        AsyncSession for database operations.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency for database sessions.

    Yields:
        AsyncSession for database operations.
    """
    async with get_session() as session:
        yield session
