# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for application configuration."""

import os
from unittest.mock import patch

import pytest


class TestSettings:
    """Tests for Settings configuration class."""

    def test_default_settings(self):
        """Test that default settings are applied."""
        # Clear cache to get fresh settings
        from src.config import Settings

        settings = Settings()

        # Note: conftest sets DATABASE_URL=sqlite:///./test.db, so we check the
        # pattern rather than exact value. Other defaults come from Settings class.
        assert "sqlite" in settings.database_url
        assert settings.sync_interval_minutes == 5
        assert settings.host == "0.0.0.0"
        assert settings.port == 8099
        assert settings.standalone_mode is True  # Set in conftest

    def test_sync_interval_validation(self):
        """Test sync interval validation bounds."""
        from src.config import Settings

        # Valid values
        settings = Settings(sync_interval_minutes=1)
        assert settings.sync_interval_minutes == 1

        settings = Settings(sync_interval_minutes=60)
        assert settings.sync_interval_minutes == 60

    def test_sync_interval_out_of_bounds(self):
        """Test sync interval rejects out of bounds values."""
        from pydantic import ValidationError
        from src.config import Settings

        with pytest.raises(ValidationError):
            Settings(sync_interval_minutes=0)

        with pytest.raises(ValidationError):
            Settings(sync_interval_minutes=61)

    def test_environment_override(self):
        """Test environment variables override defaults."""
        from src.config import Settings

        with patch.dict(os.environ, {"HOST": "127.0.0.1", "PORT": "9000"}):
            settings = Settings()
            assert settings.host == "127.0.0.1"
            assert settings.port == 9000


class TestGetSettings:
    """Tests for get_settings function."""

    def test_get_settings_returns_settings_instance(self):
        """Test get_settings returns a Settings instance."""
        from src.config import Settings, get_settings

        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_get_settings_is_cached(self):
        """Test that get_settings returns the same cached instance."""
        from src.config import get_settings

        settings1 = get_settings()
        settings2 = get_settings()
        assert settings1 is settings2
