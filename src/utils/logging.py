# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Logging configuration for RentalSync Bridge."""

import logging
import sys
from typing import TextIO

from src.config import get_settings

# Default log format
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


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

    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    console_handler.setFormatter(formatter)

    # Add handler to root logger
    root_logger.addHandler(console_handler)

    # Set specific logger levels
    _configure_library_loggers(level)


def _configure_library_loggers(app_level: int) -> None:
    """Configure third-party library log levels.

    Args:
        app_level: Application log level.
    """
    # SQLAlchemy: only show warnings unless app is DEBUG
    sqlalchemy_level = logging.DEBUG if app_level == logging.DEBUG else logging.WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(sqlalchemy_level)

    # Uvicorn: match app level but not more verbose than INFO
    uvicorn_level = max(app_level, logging.INFO)
    logging.getLogger("uvicorn").setLevel(uvicorn_level)
    logging.getLogger("uvicorn.access").setLevel(uvicorn_level)
    logging.getLogger("uvicorn.error").setLevel(uvicorn_level)

    # APScheduler: only show warnings
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    # httpx: only show warnings
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name.

    Args:
        name: Logger name, typically __name__.

    Returns:
        Logger instance.
    """
    return logging.getLogger(name)
