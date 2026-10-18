# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Logging configuration for RentalSync Bridge."""

import logging
import os
import sys
import time
from datetime import UTC, datetime
from typing import TextIO

from src.config import get_settings

# Default log format with UTC indicator
LOG_FORMAT = "%(asctime)s UTC - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Loggers that emit guest data or credentials when run at DEBUG: SQLAlchemy
# and aiosqlite echo SQL statements together with their bound parameter
# values, and the HTTP client loggers echo request URLs (which carry access
# tokens) and bodies.  They are pinned below the root level so enabling the
# add-on's ``debug`` log level never discloses personal data.
SENSITIVE_LOGGERS = (
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "sqlalchemy.orm",
    "sqlalchemy.dialects",
    "aiosqlite",
    "httpx",
    "httpcore",
)

# Level the sensitive loggers are pinned to regardless of the root level.
SENSITIVE_LOGGER_LEVEL = logging.WARNING

# Deliberately undocumented developer escape hatch.  It is intentionally not
# reachable from the add-on ``log_level`` option, so a user following the bug
# report workflow cannot turn on SQL logging by accident.
SQL_DEBUG_ENV_VAR = "RENTALSYNC_UNSAFE_SQL_LOG"

_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


class UTCFormatter(logging.Formatter):
    """Formatter that uses UTC timestamps."""

    @staticmethod
    def converter(timestamp: float | None) -> time.struct_time:
        """Convert timestamp to UTC time tuple."""
        if timestamp is None:
            timestamp = time.time()
        return datetime.fromtimestamp(timestamp, tz=UTC).timetuple()


def get_log_level() -> int:
    """Get logging level from settings.

    Returns:
        Logging level constant.
    """
    settings = get_settings()
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return level_map.get(settings.log_level.upper(), logging.INFO)


def setup_logging(
    level: int | None = None,
    stream: TextIO | None = None,
) -> None:
    """Configure application logging.

    Args:
        level: Logging level. Defaults to settings value.
        stream: Output stream. Defaults to stderr.
    """
    if level is None:
        level = get_log_level()
    if stream is None:
        stream = sys.stderr

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler
    console_handler = logging.StreamHandler(stream)
    console_handler.setLevel(level)

    # Create UTC formatter
    formatter = UTCFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    console_handler.setFormatter(formatter)

    # Add handler to root logger
    root_logger.addHandler(console_handler)

    # Set specific logger levels
    _configure_library_loggers(level)


def _sql_logging_opted_in() -> bool:
    """Check the undocumented developer opt-in for SQL logging.

    Returns:
        True when the escape hatch environment variable is truthy.
    """
    return os.environ.get(SQL_DEBUG_ENV_VAR, "").strip().lower() in _TRUTHY_VALUES


def _configure_library_loggers(app_level: int) -> None:
    """Configure third-party library log levels.

    Args:
        app_level: Application log level.
    """
    # Database and HTTP client loggers echo guest data and credentials, so
    # they stay pinned even when the application runs at DEBUG.
    for name in SENSITIVE_LOGGERS:
        logging.getLogger(name).setLevel(SENSITIVE_LOGGER_LEVEL)

    if _sql_logging_opted_in():
        logging.getLogger("sqlalchemy.engine").setLevel(app_level)

    # Uvicorn: match app level but not more verbose than INFO
    uvicorn_level = max(app_level, logging.INFO)
    logging.getLogger("uvicorn").setLevel(uvicorn_level)
    logging.getLogger("uvicorn.access").setLevel(uvicorn_level)
    logging.getLogger("uvicorn.error").setLevel(uvicorn_level)

    # APScheduler: only show warnings
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name.

    Args:
        name: Logger name, typically __name__.

    Returns:
        Logger instance.
    """
    return logging.getLogger(name)
