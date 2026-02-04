# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for logging configuration."""

import logging
from io import StringIO

from src.utils.logging import (
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    get_log_level,
    get_logger,
    setup_logging,
)


class TestGetLogLevel:
    """Tests for get_log_level function."""

    def test_returns_integer_level(self):
        """Test returns a valid integer log level."""
        level = get_log_level()
        assert isinstance(level, int)

    def test_returns_debug_level(self):
        """Test returns DEBUG level from test config."""
        # conftest sets LOG_LEVEL=DEBUG
        level = get_log_level()
        assert level == logging.DEBUG


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_configures_root_logger(self):
        """Test root logger is configured."""
        stream = StringIO()
        setup_logging(level=logging.INFO, stream=stream)

        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO
        assert len(root_logger.handlers) > 0

    def test_handler_has_formatter(self):
        """Test handler has formatter applied."""
        stream = StringIO()
        setup_logging(level=logging.INFO, stream=stream)

        root_logger = logging.getLogger()
        handler = root_logger.handlers[-1]
        assert handler.formatter is not None

    def test_removes_existing_handlers(self):
        """Test existing handlers are removed to avoid duplicates."""
        stream = StringIO()

        # Call setup twice
        setup_logging(level=logging.INFO, stream=stream)
        handler_count_1 = len(logging.getLogger().handlers)

        setup_logging(level=logging.INFO, stream=stream)
        handler_count_2 = len(logging.getLogger().handlers)

        # Should have same number of handlers (no duplicates)
        assert handler_count_1 == handler_count_2

    def test_logging_output(self):
        """Test log messages are written to stream."""
        stream = StringIO()
        setup_logging(level=logging.INFO, stream=stream)

        logger = logging.getLogger("test_output")
        logger.info("Test message")

        output = stream.getvalue()
        assert "Test message" in output
        assert "INFO" in output


class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_logger_instance(self):
        """Test returns a Logger instance."""
        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger)

    def test_logger_has_correct_name(self):
        """Test logger has the specified name."""
        logger = get_logger("my.custom.logger")
        assert logger.name == "my.custom.logger"

    def test_logger_can_log(self):
        """Test returned logger can log messages."""
        stream = StringIO()
        setup_logging(level=logging.DEBUG, stream=stream)

        logger = get_logger("test.logger")
        logger.debug("Debug message")

        output = stream.getvalue()
        assert "Debug message" in output


class TestLogConstants:
    """Tests for logging constants."""

    def test_log_format_contains_components(self):
        """Test LOG_FORMAT has expected components."""
        assert "%(asctime)s" in LOG_FORMAT
        assert "%(name)s" in LOG_FORMAT
        assert "%(levelname)s" in LOG_FORMAT
        assert "%(message)s" in LOG_FORMAT

    def test_date_format_is_valid(self):
        """Test LOG_DATE_FORMAT is a valid strftime format."""
        from datetime import datetime

        # Should not raise
        datetime.now().strftime(LOG_DATE_FORMAT)
