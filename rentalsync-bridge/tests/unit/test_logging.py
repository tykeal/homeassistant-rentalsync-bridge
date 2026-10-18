# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for logging configuration."""

import logging
from io import StringIO

from src.config import Settings
from src.utils.logging import (
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    SENSITIVE_LOGGERS,
    SQL_DEBUG_ENV_VAR,
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

    def test_accepts_lowercase_addon_option_value(self, monkeypatch):
        """Test lowercase values (as supplied by the add-on) are parsed."""
        monkeypatch.setattr(
            "src.utils.logging.get_settings",
            lambda: Settings(log_level="warning"),
        )
        assert get_log_level() == logging.WARNING

    def test_invalid_value_falls_back_to_info(self, monkeypatch):
        """Test an unrecognised level falls back to INFO instead of raising."""
        monkeypatch.setattr(
            "src.utils.logging.get_settings",
            lambda: Settings(log_level="verbose"),
        )
        assert get_log_level() == logging.INFO

    def test_empty_value_falls_back_to_info(self, monkeypatch):
        """Test an empty level (unset env var) falls back to INFO."""
        monkeypatch.setattr(
            "src.utils.logging.get_settings",
            lambda: Settings(log_level=""),
        )
        assert get_log_level() == logging.INFO


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


class TestSensitiveLibraryLoggers:
    """Tests that guest data and credentials stay out of DEBUG output."""

    def test_sensitive_loggers_pinned_at_debug(self, monkeypatch):
        """Test DEBUG never enables SQL, driver or HTTP client logging."""
        monkeypatch.delenv(SQL_DEBUG_ENV_VAR, raising=False)
        setup_logging(level=logging.DEBUG, stream=StringIO())

        for name in SENSITIVE_LOGGERS:
            library_logger = logging.getLogger(name)
            assert library_logger.level >= logging.WARNING
            assert not library_logger.isEnabledFor(logging.INFO)

    def test_sql_logging_requires_explicit_env_opt_in(self, monkeypatch):
        """Test the undocumented escape hatch re-enables SQL logging."""
        monkeypatch.setenv(SQL_DEBUG_ENV_VAR, "true")
        setup_logging(level=logging.DEBUG, stream=StringIO())

        assert logging.getLogger("sqlalchemy.engine").level == logging.DEBUG
        # The opt-in is scoped to the SQL engine only.
        assert logging.getLogger("aiosqlite").level >= logging.WARNING

    def test_addon_debug_option_does_not_enable_sql_logging(self, monkeypatch):
        """Test the add-on log_level option cannot turn on SQL logging."""
        monkeypatch.delenv(SQL_DEBUG_ENV_VAR, raising=False)
        monkeypatch.setattr(
            "src.utils.logging.get_settings",
            lambda: Settings(log_level="debug"),
        )
        setup_logging(stream=StringIO())

        assert logging.getLogger().level == logging.DEBUG
        assert logging.getLogger("sqlalchemy.engine").level >= logging.WARNING


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
